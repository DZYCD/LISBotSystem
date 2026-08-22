"""Agent 循环：多步推理引擎。

把「会话日志 + 工具运行时 + LLM」串成多步推理。核心是一个 while 循环：

    turn 开始
      step 循环:
        从 inbox 取用户消息 → 写日志 user/message
        从日志 derive_messages() → 组装请求（历史 + 工具 schema）
        调 LLM 生成 → 写日志 assistant/message
        若模型输出 tool-call:
          经 ToolRuntime 执行（审批 + 沙箱）→ 写日志 tool/result
          → 回到 step 循环（再来一轮，模型看到工具结果）
        否则 完成
    turn 结束

多步推理的本质就是这个循环：模型说「我要调工具」，harness 调完把结果
喂回去，模型再接着想，直到不再调工具。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from .llm import LlmClient, LlmResult
from .registry import ToolCall, ToolRuntime
from .session import (
    Message,
    Session,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    new_id,
)


@dataclass
class AgentOptions:
    """agent 配置。"""

    system_prompt: str = "You are a helpful agent."
    """系统提示词（兼容单层写法）。"""

    system_layers: Optional[List[str]] = None
    """分层系统提示词（缓存优化）：稳定层在前，变化层在后。

    DeepSeek 等按「请求前缀」做 prompt caching：从第一条消息开始连续相同的
    部分被缓存，命中便宜且快。因此把稳定内容（角色/工具）放前面的层，
    把每次变化的会话内容放最后，最大化前缀缓存命中率。
    若提供则优先用 layers；否则退回 system_prompt 单层。
    """

    max_steps: int = 10
    """单轮内最多执行步数（防止无限工具循环）。"""


@dataclass
class TurnResult:
    """一轮 agent 执行的结果。"""

    final_text: str = ""
    """最后一条非空 assistant 文本。"""

    steps: int = 0
    """本轮执行了多少步。"""

    reason: str = "completed"
    """结束原因：completed | max-steps | error。"""


class Agent:
    """一个 agent：持有一个会话日志，能处理用户消息（多步推理）。"""

    def __init__(
        self,
        llm: LlmClient,
        tool_runtime: Optional[ToolRuntime],
        options: Optional[AgentOptions] = None,
        session: Optional[Session] = None,
        bus=None,
        bot_id: str = "",
    ) -> None:
        self.llm = llm
        self.tool_runtime = tool_runtime
        # 默认参数不能用 AgentOptions()（定义时求值一次会共享同一实例）；
        # 用 None + 每次构造，避免跨 agent 污染。
        self.options = options if options is not None else AgentOptions()
        self.session = session or Session()
        self.bus = bus
        self.bot_id = bot_id  # 本机身份：本地工具执行的 actor
        self._system_injected = False
        """是否已把 system_prompt 注入本会话（避免重复）。"""

    # --- 对外接口 ---

    def _session_has_system(self) -> bool:
        """会话是否已注入过 system 消息（复用 session 时避免重复注入）。"""
        return any(ev.type == "system/message" for ev in self.session.events)

    def _inject_system_prompt(self) -> None:
        """注入系统提示词（分层：稳定层在前，缓存命中）。"""
        if self.options.system_layers:
            for layer in self.options.system_layers:
                if layer:
                    self.session.append("system/message", {
                        "content": [TextBlock(text=layer)],
                    })
        elif self.options.system_prompt:
            self.session.append("system/message", {
                "content": [TextBlock(text=self.options.system_prompt)],
            })

    async def run(self, user_text: str) -> TurnResult:
        """处理一条用户消息，返回最终结果。"""
        turn = self.session.seq
        self.session.append("turn/start", {"turn": turn})
        final_text = ""
        result = TurnResult()

        # 注入系统提示词（分层：稳定层在前，缓存命中；会话已注入过则跳过，避免重复）
        if not self._session_has_system():
            self._inject_system_prompt()
            self._system_injected = True

        try:
            for step in range(1, self.options.max_steps + 1):
                self.session.append("step/start", {"turn": turn, "step": step})
                # 每个 step 只处理第一条 pending 用户输入（简化：单轮单条）
                # 这里在 turn 开始时已写入 user/message（见下），后续 step 靠工具结果驱动。
                if step == 1:
                    self.session.append("user/message", {
                        "content": [TextBlock(text=user_text)],
                    })

                messages = self.session.derive_messages()
                tools = self._tool_schemas()
                llm_result = await self.llm.generate(messages, tools=tools)

                # 记录 assistant 产出
                self.session.append("assistant/message", {
                    "content": llm_result.blocks,
                })

                tool_calls = [b for b in llm_result.blocks if isinstance(b, ToolCallBlock)]
                if not tool_calls:
                    # 没有工具调用 → 本轮完成，取最后文本
                    for block in llm_result.blocks:
                        if isinstance(block, TextBlock) and block.text:
                            final_text = block.text
                    result.reason = "completed"
                    self.session.append("step/end", {"turn": turn, "step": step})
                    break

                # 串行整理：把本轮 LLM 生成的所有 tool_calls 依次执行（一个接一个，
                # 同步阻塞等回传），每个都写 tool/result。全部有结果后，assistant
                # 消息的 tool_calls 与 tool 结果数量匹配（DeepSeek 要求每个
                # tool_call 都有对应 tool 结果），且不丢失任何工具调用。
                for call in tool_calls:
                    await self._execute_tool_call(turn, step, call)

                self.session.append("step/end", {"turn": turn, "step": step})
                if step == self.options.max_steps:
                    result.reason = "max-steps"
            else:
                result.reason = "max-steps"
        except Exception as exc:  # noqa: BLE001 - 记录错误并结束本轮回溯
            result.reason = "error"
            final_text = f"error: {exc}"

        result.final_text = final_text
        result.steps = len([e for e in self.session.events if e.type == "step/start"
                            and e.data.get("turn") == turn])
        self.session.append("turn/end", {"turn": turn, "reason": result.reason})
        return result

    # --- 内部 ---

    def _tool_schemas(self) -> Optional[List[dict]]:
        if self.tool_runtime is None:
            return None
        tools = []
        for tool in self.tool_runtime._registry.list_tools():
            tools.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            })
        return tools or None

    async def _execute_tool_call(self, turn: int, step: int, call: ToolCallBlock) -> None:
        self.session.append("tool/call", {
            "turn": turn, "step": step,
            "call_id": call.id, "name": call.name, "arguments": call.arguments,
        })
        if self.bus is not None:
            self.bus.emit("tool/call", {"name": call.name, "call_id": call.id})
        # 失败闭环：无论成功还是失败，都保证有 tool/result 写回日志喂给模型，
        # 让模型能看到失败原因并自纠，而不是让循环崩溃。
        result_text = await self._run_tool_call(call)
        self.session.append("tool/result", {
            "turn": turn, "step": step,
            "content": ToolResultBlock(tool_call_id=call.id, content=result_text),
        })
        if self.bus is not None:
            self.bus.emit("tool/result", {"name": call.name, "call_id": call.id, "content": result_text})

    async def _run_tool_call(self, call: ToolCallBlock) -> str:
        """执行一次工具调用，把结果（含失败）转成文本返回。不抛异常。"""
        if self.tool_runtime is None:
            return "no tool runtime available"
        # 解析参数：坏 JSON 直接返回错误，而非静默当空参数执行。
        try:
            args = json.loads(call.arguments) if call.arguments else {}
            if not isinstance(args, dict):
                return f"[error] tool arguments must be a JSON object, got: {call.arguments!r}"
        except json.JSONDecodeError:
            return f"[error] invalid JSON arguments: {call.arguments!r}"
        tc = ToolCall(name=call.name, arguments=args, actor=self.bot_id or self.session.session_id)
        try:
            result = await self.tool_runtime.execute(tc)
        except Exception as exc:  # noqa: BLE001 - 失败必须闭环，不能冒泡
            return f"[error] tool execution failed: {exc}"
        if result.ok:
            try:
                return json.dumps(result.value, ensure_ascii=False) if result.value is not None else "ok"
            except (TypeError, ValueError):
                return f"[error] tool returned non-serializable result: {result.value!r}"
        return f"[error] {result.error}"
