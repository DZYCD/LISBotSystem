"""工具定义与注册中心。

对应 dsh 的 ctx.tools（ToolRuntime）：工具是「模型可见层」，注册进注册中心，
通过名字被发现，并关联到一个被沙箱包住的能力后端。

核心语义（对齐 dsh 的 "Everything is a plugin"）：
- register(...) 返回一个 disposer（卸载函数），卸载时清理注册；
- get / list 用于发现；
- 工具通过 backend 名字关联到能力服务，执行时经执行管线治理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .security.capability import CapabilityBackend
from .security.pipeline import ExecutionPipeline
from .security.policy import ExecutionRequest


@dataclass(frozen=True)
class ToolDefinition:
    """一个模型可见的工具。

    工具是「给模型开权限」的声明：名字、描述、参数 schema、以及它使用哪个
    能力后端。工具本身不包含安全逻辑 —— 安全由执行管线 + 沙箱后端负责。
    """

    name: str
    """工具名（模型用它来调用）。"""

    description: str
    """给模型看的工具用途描述。"""

    parameters: dict
    """参数 schema（JSON Schema 风格），描述模型可传入的参数。"""

    backend: str
    """该工具关联的能力后端名字（如 'shell'、'fs'、'web'）。"""


@dataclass(frozen=True)
class ToolCall:
    """一次模型发出的工具调用请求。"""

    name: str
    """要调用的工具名。"""

    arguments: dict
    """模型给出的参数。"""

    actor: str
    """发起者标识（会话 id）。"""


class DisposedRegistryError(RuntimeError):
    """访问已卸载的注册表条目。"""


class Registry:
    """轻量注册中心：管理工具定义与能力后端。

    两类可注册对象：
    - 工具定义（ToolDefinition）：模型可见，通过 backend 关联能力。
    - 能力后端（CapabilityBackend）：被沙箱包住的执行者。

    每次 register 返回一个 disposer（卸载函数）。卸载后再次访问该条目抛
    DisposedRegistryError。注册中心本身不执行 —— 执行由 ToolRuntime 驱动。
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._backends: Dict[str, CapabilityBackend] = {}
        self._disposed: set = set()

    # --- 注册 ---

    def register_tool(self, tool: ToolDefinition) -> Callable[[], None]:
        """注册一个工具定义，返回卸载函数。

        同名工具重复注册抛 ValueError（不允许静默覆盖 —— 显式 > 隐式）。
        若同名工具此前已卸载（热重载场景），旧的 disposed 标记被清除，
        新实例可正常访问。
        """
        if tool.name in self._tools:
            raise ValueError(f'tool "{tool.name}" is already registered')
        self._tools[tool.name] = tool
        self._disposed.discard(("tool", tool.name))
        disposed = [False]

        def disposer() -> None:
            if disposed[0]:
                return
            disposed[0] = True
            self._tools.pop(tool.name, None)
            self._disposed.add(("tool", tool.name))

        return disposer

    def register_backend(self, name: str, backend: CapabilityBackend) -> Callable[[], None]:
        """注册一个能力后端，返回卸载函数。

        同名后端重复注册抛 ValueError。
        """
        if name in self._backends:
            raise ValueError(f'backend "{name}" is already registered')
        self._backends[name] = backend
        self._disposed.discard(("backend", name))
        disposed = [False]

        def disposer() -> None:
            if disposed[0]:
                return
            disposed[0] = True
            self._backends.pop(name, None)
            self._disposed.add(("backend", name))

        return disposer

    # --- 发现 ---

    def get_tool(self, name: str) -> ToolDefinition:
        """按名字取工具定义；未注册或已卸载抛 KeyError/DisposedRegistryError。"""
        if ("tool", name) in self._disposed:
            raise DisposedRegistryError(f'tool "{name}" was disposed')
        return self._tools[name]

    def get_backend(self, name: str) -> CapabilityBackend:
        """按名字取能力后端；未注册或已卸载抛 KeyError/DisposedRegistryError。"""
        if ("backend", name) in self._disposed:
            raise DisposedRegistryError(f'backend "{name}" was disposed')
        return self._backends[name]

    def list_tools(self) -> List[ToolDefinition]:
        """返回当前所有未卸载的工具定义。"""
        return list(self._tools.values())

    def list_backends(self) -> List[str]:
        """返回当前所有未卸载的能力后端名字。"""
        return list(self._backends.keys())


class ToolRuntime:
    """把工具调用接进受保护执行管线。

    职责：给定一次 ToolCall，从注册中心解析出工具定义和能力后端，构造
    ExecutionRequest，走执行管线（审批 + 策略解析 + 沙箱执行）。

    这对应 dsh 中「模型输出 tool-call → 查注册表 → 走管线」的一步。
    """

    def __init__(self, registry: Registry, pipeline: ExecutionPipeline,
                 reload_hook: Optional[Callable[[str], bool]] = None) -> None:
        self._registry = registry
        self._pipeline = pipeline
        self._reload_hook = reload_hook

    async def execute(self, call: ToolCall):
        """受保护地执行一次工具调用。

        若配置了 reload_hook（如 PluginLoader.reload_if_changed），在解析工具
        前先校验实现源文件是否变化，变了就重载，用新版执行。
        """
        if self._reload_hook is not None:
            self._reload_hook(call.name)
        tool = self._registry.get_tool(call.name)
        backend = self._registry.get_backend(tool.backend)
        request = ExecutionRequest(
            tool_name=tool.name,
            arguments=call.arguments,
            actor=call.actor,
        )
        return await self._pipeline.execute(request, backend)
