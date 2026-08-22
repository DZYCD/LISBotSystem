"""delegate 委托工具：让模型通过 TLL transport 把任务委托给其他机器人。

这是架构创新点的核心：向外委托 = harness 的一个工具调用。
模型调用 delegate 工具，内部经 TLLTransport 能力后端发送 TASK 给目标机器人。

与本地工具（bash/fs）的区别只在治理对象：
- bash → 沙箱治理文件/进程
- delegate → 治理委托权限 + 目标合法性（白名单）
"""

from __future__ import annotations

from typing import Any

from lis_harness.registry import ToolDefinition


def create(config: dict) -> ToolDefinition:
    """构造 delegate 工具定义。

    Returns:
        关联到 'tll' 后端的 ToolDefinition。
    """
    return ToolDefinition(
        name="delegate",
        description=(
            "Delegate a task to another robot in the LIS cluster via the TLL "
            "protocol. Use this to ask another bot (e.g. sayi_996) to perform "
            "a tool or action on its side."
        ),
        parameters={
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Target bot id (must be in the delegate whitelist).",
                },
                "command": {
                    "type": "string",
                    "description": "The tool/action to run on the target bot.",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the remote tool.",
                },
            },
            "required": ["to", "command"],
        },
        backend="tll",
    )
