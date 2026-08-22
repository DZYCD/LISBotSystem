"""task_create 委托工具：让 LLM 通过 harness 原生 tool-calling 创建委托任务。

这是整合的核心：把「任务创建」从 LLM 裸拼 JSON 改为结构化工具调用。
模型调用 task_create 工具，参数被 schema 校验，内部经 TLL transport 发送
TASK 给目标机器人。

保留原有委托通道（delegate / chat_tool）——因为存在非 LLM 调用场景。
本工具是 LLM 路径的入口。
"""

from __future__ import annotations

from typing import Any

from lis_harness.registry import ToolDefinition


def create(config: dict) -> ToolDefinition:
    """构造 task_create 工具定义。

    Returns:
        关联到 'tll' 后端的 ToolDefinition。
    """
    return ToolDefinition(
        name="task_create",
        description=(
            "Create and send a delegate task to another robot in the LIS cluster "
            "via the TLL protocol. Use this to ask another bot (e.g. sayi_996) to "
            "perform a tool or action. The task is validated before sending."
        ),
        parameters={
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Target bot id (must be in the delegate whitelist from peers).",
                },
                "command": {
                    "type": "string",
                    "description": "The tool/action to run on the target bot.",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the remote tool.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Optional: reuse the current delegate-chain task id when delegating from an in-progress task. Keeps the chain on one task id.",
                },
            },
            "required": ["to", "command"],
        },
        backend="tll",
    )
