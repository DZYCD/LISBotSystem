"""TLL transport 插件：把 LIS-harness 接到 TLL 协议（机器人间通信）。

核心思想（架构创新点）：向外委托 = harness 的一个工具调用。
- TLL 协议作为一个「能力后端」（CapabilityBackend），供 task_create 工具调用。
- 本地工具（bash/fs）走沙箱治理；TLL 委托工具治理「委托权限 + 目标合法性」。
- 两者在 harness 眼里都是工具 —— LLM-harness 成为核心，TLL 是它的 transport 插件。

同步阻塞语义：task_create 被调时，发 TASK 委托后**同步等待网络回传**，
回传结果作为普通 tool-result 返回给 LLM。TASK id 可复用（LLM 在被委托的
task 上下文中再委托时，复用当前 task.id 保持委托链连贯）。

本实现是模拟版（不接真实 MQTT）：用内存收发 TASK，验证分层可行。
生产环境应替换成真实 mqtt_transport（paho-mqtt），用 handle_response
驱动回传。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..security.capability import CapabilityBackend, ExecutionResult
from ..security.policy import ExecutionRequest, SandboxPolicy


def _now_iso() -> str:
    """返回 ISO-8601 UTC 时间字符串（对齐真实 TLL 的 created_at 格式）。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TLLTask:
    """一次委托任务（对齐真实 TLL 的 Task/TLLjson 结构）。

    与 tll_protocol/core.py 的 Task.to_dict() 兼容：
    - 命令载荷嵌套在 tlljson 里（from_bot/command/to/params）
    - created_at 用 ISO-8601 UTC 字符串
    - 键名对齐：id（非 task_id）、from_bot（非 from）、to（非 to_bot）
    """

    task_id: str
    from_bot: str
    to: str
    command: str
    params: Dict[str, Any]
    task_type: str = "tool"
    status: str = "pending"
    current_agent: str = ""
    prev_hop: Optional[str] = None
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为真实 TLL Task 结构（可被 core.Task.from_dict 解析）。"""
        return {
            "id": self.task_id,
            "created_at": self.created_at,
            "type": self.task_type,
            "status": self.status,
            "from_bot": self.from_bot,
            "current_agent": self.current_agent or self.from_bot,
            "prev_hop": self.prev_hop,
            "sender_group": None,
            "tlljson": {
                "from_bot": self.from_bot,
                "command": self.command,
                "to": self.to,
                "params": self.params,
                "task_func": None,
            },
            "output": None,
            "result": None,
            "error": None,
            "trace": {"trace_id": "", "hops": []},
            "forward_target": None,
            "logs": [],
            "route": [],
            "delegate_count": 0,
            "last_target": "",
            "original_text": None,
            "reviewed": False,
            "delegated": False,
        }


@dataclass
class TLLTransportConfig:
    """TLL transport 配置。"""

    my_bot_id: str = "agent/eiar_001"
    """当前机器人 ID。"""

    # 可委托的目标机器人（白名单）
    peers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    """目标机器人 -> 其可用工具列表。示例：{"agent/sayi_996": {"tools": ["web_search"]}}"""

    timeout_s: float = 60.0
    """同步等待网络回传的超时秒数。超时视为委托失败。"""


class TLLTransport(CapabilityBackend):
    """TLL transport 能力后端（同步阻塞模拟）。

    供 task_create 工具调用。execute 收到委托请求后，校验目标合法性，
    发送 TASK，**同步等待回传**，把回传结果作为 ExecutionResult 返回。
    模拟场景：发送后同步调 handler 得结果；真实场景：发送后等 MQTT 回传
    消息调 handle_response 填充等待结果。
    """

    name = "tll"

    def __init__(self, config: TLLTransportConfig = TLLTransportConfig()) -> None:
        self._config = config
        self.sent_tasks: List[TLLTask] = []
        """已发送的委托任务（内省用）。"""
        self._peer_handlers: Dict[str, Dict[str, Callable[[Dict], Any]]] = {}
        """模拟：每个 peer 机器人能响应什么命令。"""
        self._pending: Dict[str, asyncio.Future] = {}
        """task_id -> 等待回传的 future（同步阻塞等待机制）。"""

    def register_peer_handler(
        self,
        bot_id: str,
        command: str,
        handler: Callable[[Dict], Any],
    ) -> None:
        """注册一个模拟目标机器人的命令处理函数（模拟远端响应）。"""
        self._peer_handlers.setdefault(bot_id, {})[command] = handler

    def handle_response(self, task_id: str, result: Any) -> None:
        """填充一个等待中的回传结果（供真实 MQTT 回传驱动调用）。"""
        fut = self._pending.get(task_id)
        if fut is not None and not fut.done():
            fut.set_result(result)

    async def execute(
        self,
        request: ExecutionRequest,
        policy: SandboxPolicy,
    ) -> ExecutionResult:
        args = request.arguments
        to_bot = args.get("to")
        command = args.get("command")
        params = args.get("params") or {}
        reuse_task_id = args.get("task_id")  # 复用当前委托链的 task.id（可空）

        # 目标合法性治理：目标必须在白名单（peers）内
        if to_bot not in self._config.peers:
            return ExecutionResult(
                ok=False,
                denied=True,
                error=f"[tll: denied] target {to_bot} is not in the delegate whitelist",
            )
        if command is None:
            return ExecutionResult(ok=False, error="tll: requires command", denied=False)

        # A4 修复：校验 command 是否在该 peer 声明的能力（tools）内
        peer = self._config.peers[to_bot]
        peer_tools = peer.get("tools") or []
        allowed_names = {t.get("name") if isinstance(t, dict) else t for t in peer_tools}
        if allowed_names and command not in allowed_names:
            return ExecutionResult(
                ok=False,
                denied=True,
                error=(
                    f"[tll: denied] command {command!r} is not in {to_bot}'s declared "
                    f"tools {sorted(allowed_names)}"
                ),
            )

        # 发送 TASK：TASK id 复用（保持委托链连贯）或新建
        task_id = reuse_task_id or uuid.uuid4().hex[:12]
        task = TLLTask(
            task_id=task_id,
            from_bot=self._config.my_bot_id,
            to=to_bot,
            command=command,
            params=params,
        )
        self.sent_tasks.append(task)

        # 同步阻塞等待回传
        fut = asyncio.get_running_loop().create_future()
        self._pending[task_id] = fut
        try:
            # 模拟：直接派发远端 handler 得结果（生产改为发 MQTT，等 handle_response）
            result = self._dispatch_remote(task)
            if result is not None:
                fut.set_result(result)
            # 若派发未立即给结果，则等待 handle_response 填充
            result = await asyncio.wait_for(asyncio.shield(fut), timeout=self._config.timeout_s)
        finally:
            self._pending.pop(task_id, None)

        return ExecutionResult(ok=True, value={
            "task_id": task_id,
            "to": to_bot,
            "command": command,
            "result": result,
        })

    def _dispatch_remote(self, task: TLLTask) -> Any:
        handlers = self._peer_handlers.get(task.to, {})
        handler = handlers.get(task.command)
        if handler is None:
            return {"error": f"remote {task.to} has no handler for {task.command}"}
        return handler(task.params)

    # --- transport 生命周期 ---

    def mount(self) -> None:
        """挂载 transport（模拟建立连接）。生产环境在此连接 MQTT。"""
        pass

    def shutdown(self) -> None:
        """关闭 transport（模拟断开）。生产环境在此断开 MQTT。"""
        pass
