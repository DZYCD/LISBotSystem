"""插件加载器：YAML 声明 + Python 实现，支持热重载。

分层（关键设计）：
- 声明层：YAML 描述「挂哪些工具/后端、各自配置、实现源文件」。
- 实现层：每个工具/后端一个 Python 模块（实现工厂函数 create(config)）。

热重载模型（调用前惰性校验，优于后台监视）：
- 加载时快照每个实现源文件的 mtime，收集该注册的 disposer。
- reload_if_changed(tool) 在调用前 stat 源文件；mtime 变了就重载
  （dispose 旧注册 → 重新加载模块 → 用新实现重新注册）。
- 执行中的调用持有旧实例快照，不受重载影响；新调用用新版。

零依赖（除 PyYAML）、无常驻线程、只花一次 stat。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml

from .registry import Registry, ToolDefinition
from .security.capability import CapabilityBackend


class DisposerSet:
    """一组 disposer 的集合，可整体卸载。对应 Cordis 的 fiber/plugin 生命周期。"""

    def __init__(self) -> None:
        self._disposers: List[Callable[[], None]] = []
        self._unloaded = False

    def add(self, disposer: Callable[[], None]) -> None:
        if self._unloaded:
            raise RuntimeError("cannot add to an unloaded set")
        self._disposers.append(disposer)

    def unload(self) -> None:
        if self._unloaded:
            return
        self._unloaded = True
        for disposer in reversed(self._disposers):
            disposer()
        self._disposers.clear()


@dataclass
class ToolWatch:
    """一个工具实现源文件的监视状态。"""

    module_path: str
    """实现模块 import 路径（如 tools.bash_tool）。"""

    file_path: Path
    """实现源文件路径（用于 stat）。"""

    mtime: float
    """上次加载时的源文件修改时间。"""

    spec: dict
    """该工具的 YAML spec（重载时用于重建）。"""

    disposers: DisposerSet = field(default_factory=DisposerSet)
    """该工具注册进 Registry 的全部 disposer。"""

    def changed_since(self) -> bool:
        """实现源文件 mtime 是否比上次加载时新。"""
        try:
            return self.file_path.stat().st_mtime > self.mtime + 1e-9
        except OSError:
            # 源文件被删除：视为需要重载。
            return True


class PluginLoader:
    """从 YAML 声明加载工具/后端，并支持调用前热重载。

    YAML 结构：
        backends:
          <name>:
            implements: <module.path>
            config: {...}
        tools:
          <name>:
            implements: <module.path>   # 被监视的源文件
            backend: <backend_name>
            description: ...
            parameters: {...}
            config: {...}
    """

    def __init__(self, registry: Registry, base_dir: Path) -> None:
        self._registry = registry
        self._base_dir = base_dir
        self._watches: Dict[str, ToolWatch] = {}
        """工具名 -> 工具源文件监视。"""
        self._modules: Dict[str, object] = {}
        """模块加载缓存；reload 用 importlib.reload 重执行顶层。"""

    # --- 加载 ---

    def load(self, yaml_path: Path) -> None:
        """从 YAML 加载全部后端和工具，注册进 Registry。"""
        data = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
        config = data or {}
        for name, spec in (config.get("backends") or {}).items():
            self._load_backend(name, spec)
        for name, spec in (config.get("tools") or {}).items():
            self._load_tool(name, spec)

    def _load_backend(self, name: str, spec: dict) -> None:
        factory = self._factory(spec["implements"])
        backend = factory(spec.get("config") or {})
        if not isinstance(backend, CapabilityBackend):
            raise TypeError(f'backend "{name}" factory must return a CapabilityBackend')
        # 后端注册也收集 disposer，供整体卸载；后端通常不在调用前重载。
        self._registry.register_backend(name, backend)

    def _load_tool(self, name: str, spec: dict) -> None:
        module_path = spec["implements"]
        factory = self._factory(module_path)
        tool = factory(spec.get("config") or {})
        if not isinstance(tool, ToolDefinition):
            raise TypeError(f'tool "{name}" factory must return a ToolDefinition')
        disposer = self._registry.register_tool(tool)

        module_file = self._module_file(module_path)
        watch = ToolWatch(
            module_path=module_path,
            file_path=module_file,
            mtime=self._mtime(module_file),
            spec=spec,
        )
        watch.disposers.add(disposer)
        self._watches[name] = watch

    def _factory(self, module_path: str) -> Callable:
        """导入实现模块，返回其 create 工厂。"""
        module = self._modules.get(module_path)
        if module is None:
            module = importlib.import_module(module_path)
            self._modules[module_path] = module
        return module.create

    def _module_file(self, module_path: str) -> Path:
        module = self._modules[module_path]
        file_attr = getattr(module, "__file__", None)
        if file_attr is not None:
            return Path(file_attr)
        return self._base_dir / f"{module_path.rsplit('.', 1)[-1]}.py"

    def _mtime(self, path: Path) -> float:
        return path.stat().st_mtime

    # --- 调用前重载 ---

    def reload_if_changed(self, tool_name: str) -> bool:
        """调用前校验：工具实现源文件是否变化，变了就重载。

        Returns:
            是否执行了重载。
        """
        watch = self._watches.get(tool_name)
        if watch is None:
            return False
        if not watch.changed_since():
            return False
        self.reload_tool(tool_name)
        return True

    def reload_tool(self, tool_name: str) -> None:
        """卸载并重载一个工具（含其实现模块）。"""
        watch = self._watches.get(tool_name)
        if watch is None:
            return
        # 1. 卸载旧注册
        watch.disposers.unload()
        # 2. 重新执行实现模块顶层（importlib.reload）
        module_path = watch.module_path
        module = self._modules.get(module_path)
        if module is not None:
            try:
                self._modules[module_path] = importlib.reload(module)
            except Exception:
                # 模块 reload 失败：保留旧模块可用，但移除监视（避免反复失败）。
                self._watches.pop(tool_name, None)
                raise
        # 3. 用缓存的 spec 重建工具（新 mtime）
        self._watches.pop(tool_name, None)
        self._load_tool(tool_name, watch.spec)

    def watches(self) -> Dict[str, ToolWatch]:
        """返回当前全部工具监视（测试/内省用）。"""
        return dict(self._watches)
