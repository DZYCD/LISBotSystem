"""示例工具实现：bash 工具。

每个工具/后端实现模块约定导出 create(config) 工厂，返回一个对象。
- 工具实现返回 ToolDefinition（含描述、参数、关联后端）。
- 后端实现返回 CapabilityBackend。

本文件作为热重载测试的目标：测试通过改写此文件并触碰 mtime，观察
reload_if_changed 是否用新实现重建工具。
"""

from __future__ import annotations

from typing import Any

from lis_harness.registry import ToolDefinition


def create(config: dict) -> ToolDefinition:
    """构造 bash 工具定义。

    Returns:
        关联到 'shell' 后端的 ToolDefinition。
    """
    return ToolDefinition(
        name="bash",
        description="Run a shell command within the workspace sandbox.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run."},
            },
            "required": ["command"],
        },
        backend="shell",
    )
