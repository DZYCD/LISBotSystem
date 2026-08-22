"""会话日志：append-only 事件流，唯一真相源。

核心原则（对齐 dsh）：模型能看到的东西必须能从日志重建。
- 所有事件 append-only 写入 log，每条带单调递增 seq。
- derive_messages() 把日志投影为发给模型的对话历史。
- 日志是记忆、可追溯、可回放、可恢复的根源。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


# --- 消息模型 ---

@dataclass
class TextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class ToolCallBlock:
    type: str = "tool-call"
    id: str = ""
    name: str = ""
    arguments: str = ""  # 模型输出的原始参数（JSON 字符串）


@dataclass
class ToolResultBlock:
    type: str = "tool-result"
    tool_call_id: str = ""
    content: str = ""


@dataclass
class ReasoningBlock:
    """DeepSeek 思考模型的推理链内容（reasoning_content）。

    思考模型在 assistant 消息里返回 reasoning_content，后续请求必须回传，
    否则 API 400。此块保存它，_message_to_api 会放回 assistant 消息顶层字段。
    """
    type: str = "reasoning"
    text: str = ""


ContentBlock = Union[TextBlock, ToolCallBlock, ToolResultBlock, ReasoningBlock]


@dataclass
class Message:
    role: str  # 'user' | 'assistant'
    content: List[ContentBlock]
    id: str = field(default_factory=lambda: new_id("msg_"))


# --- 会话事件 ---

@dataclass
class SessionEvent:
    type: str
    data: Dict[str, Any]
    seq: int
    time: float


class Session:
    """一个会话的 append-only 日志。

    每条事件有单调递增 seq。事件类型：turn/start、user/message、
    assistant/chunk、assistant/message、tool/call、tool/result、step/start、
    step/end、turn/end。
    """

    def __init__(self, session_id: Optional[str] = None) -> None:
        self.session_id = session_id or new_id("session_")
        self._log: List[SessionEvent] = []

    # --- 写入 ---

    def append(self, event_type: str, data: Dict[str, Any]) -> SessionEvent:
        """追加一条事件，分配 seq。"""
        event = SessionEvent(type=event_type, data=data, seq=len(self._log), time=time.time())
        self._log.append(event)
        return event

    # --- 读取 ---

    @property
    def events(self) -> List[SessionEvent]:
        return list(self._log)

    @property
    def seq(self) -> int:
        return len(self._log)

    # --- 投影 ---

    def derive_messages(self) -> List[Message]:
        """把日志投影为发给模型的对话历史。

        规则：
        - system/message → system 角色消息
        - user/message → user 角色消息
        - assistant/message → assistant 角色消息
        - tool/result → 作为 user 角色的一条 tool-result 消息（闭环工具结果）
        其他事件（turn/start、chunk 等）不参与投影。
        """
        messages: List[Message] = []
        for event in self._log:
            if event.type == "system/message":
                messages.append(Message(role="system", content=event.data["content"]))
            elif event.type == "user/message":
                messages.append(Message(role="user", content=event.data["content"]))
            elif event.type == "assistant/message":
                messages.append(Message(role="assistant", content=event.data["content"]))
            elif event.type == "tool/result":
                messages.append(Message(role="user", content=[event.data["content"]]))
        return messages

    # --- 持久化 / 回放 ---

    def dump(self) -> Dict[str, Any]:
        """把日志序列化为可持久化的 JSON 结构。"""
        return {
            "session_id": self.session_id,
            "events": [self._event_to_dict(e) for e in self._log],
        }

    def _event_to_dict(self, event: SessionEvent) -> Dict[str, Any]:
        data = dict(event.data)
        # content 可能是「块列表」（user/assistant 消息）或「单个 ToolResultBlock」
        content = data.get("content")
        if isinstance(content, list):
            data["content"] = [_block_to_dict(b) for b in content]
        elif isinstance(content, ToolResultBlock):
            data["content"] = _block_to_dict(content)
        return {"type": event.type, "data": data, "seq": event.seq}

    @classmethod
    def restore(cls, dump: Dict[str, Any]) -> "Session":
        """从 dump 恢复会话（回放日志）。"""
        session = cls(dump["session_id"])
        for ev in dump["events"]:
            data = dict(ev["data"])
            content = data.get("content")
            if isinstance(content, list):
                data["content"] = [_block_from_dict(b) for b in content]
            elif isinstance(content, dict):
                data["content"] = _block_from_dict(content)
            session._log.append(
                SessionEvent(type=ev["type"], data=data, seq=ev["seq"], time=0.0)
            )
        return session


def _block_to_dict(block: ContentBlock) -> Dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolCallBlock):
        return {"type": "tool-call", "id": block.id, "name": block.name, "arguments": block.arguments}
    if isinstance(block, ToolResultBlock):
        return {"type": "tool-result", "tool_call_id": block.tool_call_id, "content": block.content}
    if isinstance(block, ReasoningBlock):
        return {"type": "reasoning", "text": block.text}
    raise TypeError(f"unknown block type: {block}")


def _block_from_dict(d: Dict[str, Any]) -> ContentBlock:
    t = d["type"]
    if t == "text":
        return TextBlock(text=d.get("text", ""))
    if t == "tool-call":
        return ToolCallBlock(id=d.get("id", ""), name=d.get("name", ""), arguments=d.get("arguments", ""))
    if t == "tool-result":
        return ToolResultBlock(tool_call_id=d.get("tool_call_id", ""), content=d.get("content", ""))
    if t == "reasoning":
        return ReasoningBlock(text=d.get("text", ""))
    raise TypeError(f"unknown block type: {t}")
