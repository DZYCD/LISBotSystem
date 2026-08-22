"""DeepSeek LLM 适配器：把 harness 的模型接口接到 DeepSeek API。

用标准库 urllib（零第三方依赖）发 HTTP 请求。对应 dsh 的
LlmAdapter / llm-deepseek 插件：把「模型供应商」翻译成 harness 统一接口。

本适配器实现 LlmClient 抽象：
- generate(messages, tools) → 把 harness 消息/工具 schema 转成 OpenAI 兼容
  chat/completions 请求 → 调 DeepSeek API → 把响应转回 LlmResult。

环境变量：
- DEEPSEEK_API_KEY：API 密钥（必需）。
- DEEPSEEK_BASE_URL：可选，默认 https://api.deepseek.com。
- DEEPSEEK_MODEL：可选，默认 deepseek-chat。
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List, Optional

from ..llm import LlmClient, LlmResult
from ..session import (
    ContentBlock,
    Message,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekError(RuntimeError):
    """DeepSeek API 调用失败。"""


class DeepSeekClient(LlmClient):
    """调用 DeepSeek（OpenAI 兼容）API 的适配器。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout_ms: int = 60_000,
        http_opener=None,
    ) -> None:
        """构造 DeepSeek 客户端。

        Args:
            api_key: API 密钥；默认读 DEEPSEEK_API_KEY 环境变量。
            base_url: API 根地址（不含 /chat/completions）。
            model: 模型名。
            timeout_ms: 请求超时（毫秒）。
            http_opener: 可注入的 urllib opener（测试用）。
        """
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_ms = timeout_ms
        self._opener = http_opener or urllib.request.build_opener()
        if not self.api_key:
            raise DeepSeekError("DEEPSEEK_API_KEY is required (set env var or pass api_key)")

    # --- LlmClient 接口 ---

    async def generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LlmResult:
        payload = self._build_payload(messages, tools)
        response = self._post(payload)
        return self._parse_response(response)

    async def generate_knowledge_points(self, messages: List[Dict]) -> List[Dict]:
        """把对话历史压缩为多个知识点（供长期记忆 / 知识库沉淀）。

        Args:
            messages: [{role, content}] 列表。

        Returns:
            知识点列表：[{topic, summary, keywords, source}]。
        """
        prompt = [
            "请将以下对话历史按领域拆分为多个知识点，每个知识点包含 topic、summary（200字内）和 keywords。",
            "要求：",
            "1. 识别对话中涉及的不同主题（如编程、网络、文档处理、任务调度等）",
            "2. 每个主题生成一个知识点，summary 需包含关键事实和决策",
            "3. 只输出 JSON：{\"knowledge_points\": [{\"topic\": \"\", \"summary\": \"\", \"keywords\": []}]}",
            "\n对话历史：",
        ]
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")[:500]
            prompt.append(f"[{role}] {content}")

        payload = self._build_payload(
            [Message(role="user", content=[TextBlock(text="\n".join(prompt))])],
            tools=None,
        )
        # 降低 temperature 使输出更结构化
        payload["temperature"] = 0.2
        response = self._post(payload)
        content = (response.get("choices") or [{}])[0].get("message", {}).get("content", "")
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # 兜底：从 JSON 包裹中截取
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(content[start : end + 1])
                except json.JSONDecodeError:
                    data = {}
            else:
                data = {}
        points = (data or {}).get("knowledge_points", [])
        for p in points:
            p.setdefault("topic", "通用")
            p.setdefault("keywords", [])
            p.setdefault("source", "llm_compress")
        return points

    # --- 请求构造 ---

    def _build_payload(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_to_api(m) for m in messages],
        }
        if tools:
            payload["tools"] = [self._tool_to_api(t) for t in tools]
            payload["tool_choice"] = "auto"
        return payload

    def _message_to_api(self, message: Message) -> Dict[str, Any]:
        """把 harness Message 转成 OpenAI 兼容消息。"""
        if message.role == "system":
            text_parts = [b.text for b in message.content if isinstance(b, TextBlock)]
            return {"role": "system", "content": "\n".join(text_parts) or ""}
        if message.role == "user":
            # 检查是否含 tool-result 块（模型工具结果消息）
            blocks = message.content
            tool_result = [b for b in blocks if isinstance(b, ToolResultBlock)]
            if tool_result:
                return {
                    "role": "tool",
                    "tool_call_id": tool_result[0].tool_call_id,
                    "content": tool_result[0].content,
                }
            text_parts = [b.text for b in blocks if isinstance(b, TextBlock)]
            return {"role": "user", "content": "\n".join(text_parts) or ""}
        if message.role == "assistant":
            content_parts = [b.text for b in message.content if isinstance(b, TextBlock)]
            reasoning_parts = [b.text for b in message.content if isinstance(b, ReasoningBlock)]
            # 所有 tool_calls 都保留：Agent 串行执行每个 tool_call 并写 tool/result，
            # 因此每个 tool_call 都有对应 tool 结果，DeepSeek 配对要求满足。
            tool_calls = [
                {
                    "id": b.id,
                    "type": "function",
                    "function": {
                        "name": b.name,
                        "arguments": b.arguments,
                    },
                }
                for b in message.content if isinstance(b, ToolCallBlock)
            ]
            msg: Dict[str, Any] = {
                "role": "assistant",
                "content": "\n".join(content_parts) if content_parts else None,
            }
            # DeepSeek 思考模式：reasoning_content 必须回传，否则 API 400
            if reasoning_parts:
                msg["reasoning_content"] = "\n".join(reasoning_parts)
            if tool_calls:
                msg["tool_calls"] = tool_calls
            return msg
        raise ValueError(f"unsupported role: {message.role}")

    def _tool_to_api(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        params = tool.get("parameters")
        # 空/非法 schema 兜底成合法 object schema（DeepSeek/OpenAI 拒绝空 dict）
        if not isinstance(params, dict) or params.get("type") != "object":
            params = {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": params,
            },
        }

    # --- HTTP ---

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with self._opener.open(req, timeout=self.timeout_ms / 1000) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise DeepSeekError(f"DeepSeek API HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise DeepSeekError(f"DeepSeek API request failed: {exc}") from exc
        return json.loads(body)

    # --- 响应解析 ---

    def _parse_response(self, response: Dict[str, Any]) -> LlmResult:
        """把 OpenAI 兼容响应转回 LlmResult。"""
        choices = response.get("choices") or []
        if not choices:
            raise DeepSeekError("DeepSeek API returned no choices")
        message = choices[0].get("message") or {}
        blocks: List[ContentBlock] = []

        # 思考模型：reasoning_content（推理链）要保存并回传，否则后续请求 400
        reasoning = message.get("reasoning_content")
        if reasoning:
            blocks.append(ReasoningBlock(text=str(reasoning)))

        content = message.get("content")
        if content:
            blocks.append(TextBlock(text=str(content)))

        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            blocks.append(ToolCallBlock(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                arguments=fn.get("arguments", "{}"),
            ))

        return LlmResult(blocks=blocks)
