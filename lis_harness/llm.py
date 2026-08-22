"""LLM 客户端抽象：agent 循环通过它调用模型。

对应 dsh 的 ctx.llm / LlmAdapter。最小可用版：
- LlmClient 抽象：给定消息历史 + 可用工具 schema，返回一轮生成的块
  （文本块 和/或 工具调用块）。
- MockLlmClient：模拟实现，供循环和日志测试跑通。真实模型以后填。

流式（stream 逐 chunk）这里简化为一次性返回全部块，但保留接口抽象，
后续可扩展为流式。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .session import ContentBlock, Message, TextBlock, ToolCallBlock, new_id


@dataclass
class LlmResult:
    """一轮模型生成的产出。"""

    blocks: List[ContentBlock]
    """生成的块（文本 和/或 tool-call）。"""


@dataclass
class LlmClient(ABC):
    """模型客户端抽象。"""

    @abstractmethod
    async def generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LlmResult:
        """给定对话历史与可用工具，返回一轮生成的块。"""
        raise NotImplementedError


@dataclass
class MockLlmClient(LlmClient):
    """模拟模型：根据脚本决定输出文本或工具调用。

    script 是一个回调，接收 messages 和 tools，返回一个 LlmResult。
    这样测试/演示可以精确控制模型行为（先调工具，再给最终答案）。
    """

    script: Callable[[List[Message], Optional[List[Dict[str, Any]]]], LlmResult]
    """决定输出的脚本。"""

    async def generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LlmResult:
        return self.script(messages, tools)


def call_tool(name: str, arguments: dict) -> ToolCallBlock:
    """便捷构造一个 tool-call 块。"""
    return ToolCallBlock(
        id=new_id("call_"),
        name=name,
        arguments=json.dumps(arguments),
    )


def text(text: str) -> TextBlock:
    return TextBlock(text=text)
