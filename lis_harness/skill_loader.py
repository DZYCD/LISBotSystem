"""技能加载器：让 harness 从 skills/ 目录动态注册工具（单一来源）。

单一来源原则：工具声明在 skills/<name>/tool.yaml（+ tool.py 实现）。
- TLL 侧：handler_map[skill] = tool.py.handle （外部机器人调用）
- harness 侧：Registry 从同一份 tool.yaml 生成 ToolDefinition （LLM 调用）

关键：harness 需要 parameters schema 才能让模型正确调用；而现有 tool.yaml
只有 name + description。因此：
1. 优先读 tool.yaml 里的可选 `parameters` 段（若声明了）。
2. 否则用宽松 schema（object，允许任意属性）——保证能调用，只是模型不知道
   确切参数名。将来可在 tool.yaml 补充 parameters 提升可靠性。

ToolDefinition 关联一个本地执行后端（SkillBackend），它真正调用 tool.py 的
handle(params)。这样 TLL 和 harness 共享同一份工具实现。
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from .registry import Registry, ToolDefinition
from .security.capability import CapabilityBackend, ExecutionResult
from .security.policy import ExecutionRequest, SandboxPolicy


@dataclass
class SkillSpec:
    """一个工具（技能）的声明。"""

    name: str
    description: str
    directory: Path
    """技能目录（含 tool.yaml + tool.py）。"""

    parameters: Dict[str, Any]
    """参数 schema；若无声明则为宽松 schema。"""

    access: Dict[str, Any]
    """访问控制（来自 bot.yaml 的 tools 段 access）。"""


class SkillBackend(CapabilityBackend):
    """本地执行后端：调用 tool.py 的 handle(params, bot, task)。

    TLL 和 harness 共享这个执行函数（单一来源）。每个 skill 一个实例。
    bot_context 为可选：旧 skill 的 handle 需要 bot 上下文（如 ping 返回注册信息）。
    """

    name = "skill"

    def __init__(self, skill_name: str, handler: Callable, bot_context: Any = None) -> None:
        self.skill_name = skill_name
        self._handler = handler
        self.bot_context = bot_context

    async def execute(
        self,
        request: ExecutionRequest,
        policy: SandboxPolicy,
    ) -> ExecutionResult:
        try:
            sig = inspect.signature(self._handler)
            params = dict(request.arguments or {})
            kwargs = {}
            if "bot" in sig.parameters:
                kwargs["bot"] = self.bot_context
            if "task" in sig.parameters:
                kwargs["task"] = None
            result = self._handler(params, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 工具错误转成结果
            return ExecutionResult(ok=False, error=f"{self.skill_name} failed: {exc}", denied=False)
        if isinstance(result, dict):
            # 兼容现有工具返回 {status, info} 格式
            if result.get("status") == "error":
                return ExecutionResult(ok=False, error=result.get("info", "error"), denied=False)
            return ExecutionResult(ok=True, value=result)
        return ExecutionResult(ok=True, value={"result": result})


class SkillLoader:
    """从 skills/ 目录扫描工具，注册进 harness Registry。

    Args:
        skills_dir: skills 根目录（如 bots/eiar_002/skills）。
        tool_access: bot.yaml tools 段的 access 映射（name -> access）。
    """

    def __init__(self, skills_dir: Path, tool_access: Optional[Dict[str, Dict]] = None) -> None:
        self._skills_dir = Path(skills_dir)
        self._tool_access = tool_access or {}
        self._handlers: Dict[str, Callable] = {}

    def scan(self) -> List[SkillSpec]:
        """扫描 skills 目录，返回发现的工具声明。"""
        specs = []
        if not self._skills_dir.is_dir():
            return specs
        for entry in sorted(self._skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            yaml_path = entry / "tool.yaml"
            py_path = entry / "tool.py"
            if not yaml_path.is_file() or not py_path.is_file():
                continue
            try:
                meta = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            name = meta.get("name") or entry.name
            specs.append(SkillSpec(
                name=name,
                description=meta.get("description", ""),
                directory=entry,
                parameters=meta.get("parameters") or _loose_schema(),
                access=self._tool_access.get(name, {}),
            ))
        return specs

    def load_into(self, registry: Registry) -> List[Callable[[], None]]:
        """把扫描到的工具注册进 harness Registry，返回 disposer 列表。"""
        disposers = []
        for spec in self.scan():
            handler = self._load_handler(spec.directory)
            if handler is None:
                continue
            backend_name = f"skill:{spec.name}"
            backend = SkillBackend(spec.name, handler)
            # 注册后端 + 工具（每个 skill 一对）
            disposers.append(registry.register_backend(backend_name, backend))
            tool = ToolDefinition(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
                backend=backend_name,
            )
            disposers.append(registry.register_tool(tool))
            self._handlers[spec.name] = handler
        return disposers

    def load_one(self, registry: Registry, name: str) -> Optional[Callable[[], None]]:
        """按名字加载单个 skill，注册进 Registry，返回 disposer（找不到返回 None）。"""
        for spec in self.scan():
            if spec.name != name:
                continue
            handler = self._load_handler(spec.directory)
            if handler is None:
                return None
            backend_name = f"skill:{spec.name}"
            registry.register_backend(backend_name, SkillBackend(spec.name, handler))
            registry.register_tool(ToolDefinition(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
                backend=backend_name,
            ))
            self._handlers[spec.name] = handler
            return lambda: None  # 简化 disposer（单次加载）
        return None

    def _load_handler(self, directory: Path) -> Optional[Callable]:
        """加载 tool.py 的 handle 函数。"""
        py_path = directory / "tool.py"
        spec = importlib.util.spec_from_file_location(f"skill_{directory.name}", py_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        # 把 skill 的父目录（skills/）加进 sys.path，便于 tool.py 里的共享实现导入
        parent = directory.parent
        if str(parent) not in sys.path:
            sys.path.insert(0, str(parent))
        try:
            spec.loader.exec_module(module)
        except Exception:
            return None
        return getattr(module, "handle", None)


def _loose_schema() -> Dict[str, Any]:
    """无 parameters 声明时的宽松 schema（允许任意参数）。"""
    return {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
