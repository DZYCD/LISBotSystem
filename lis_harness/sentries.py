"""哨兵插件：展示三种运行模式。

哨兵 = 插件。通过统一的 mount(bus, registry) 部署，返回 disposer。
三种运行模式：

1. 常驻激活哨兵（Guard）：挂载即持续工作，不依赖被调用。如沙箱 —— 每次
   工具调用都检查，一直站岗。
2. 事件驱动哨兵（Listener）：挂载后待命，事件来了才被触发。通过 bus.on 订阅。
3. 懒激活哨兵（Worker）：平时待命，被显式调用才执行核心逻辑。如 LLM ——
   agent 调 generate 才发请求。

这些哨兵通过 bus 交流，不需要 LLM 在场。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .events import EventBus
from .registry import Registry


@dataclass
class Sentry:
    """哨兵基类：统一 mount 契约。"""

    name: str

    def mount(self, bus: EventBus, registry: Registry) -> Callable[[], None]:
        """部署哨兵，返回卸载函数。子类实现。"""
        raise NotImplementedError


@dataclass
class GuardSentry(Sentry):
    """常驻激活哨兵（Guard）：挂载即持续工作。

    类比沙箱：一直站岗。这里演示一个"活动计数器"——每次工具调用经过时，
    它都记录一次。它不需要被谁调用，挂上就在跑。
    """

    call_count: int = 0
    """已见证的工具调用次数。"""

    def mount(self, bus: EventBus, registry: Registry) -> Callable[[], None]:
        # 常驻哨兵通过订阅"所有工具调用"事件持续工作。
        # 一旦挂载，它就一直监听，任何 tool/call 都触发它累加。
        def on_tool_call(data: Dict[str, Any]) -> None:
            self.call_count += 1

        return bus.on("tool/call", on_tool_call)


@dataclass
class ListenerSentry(Sentry):
    """事件驱动哨兵（Listener）：订阅事件，与其他插件交流。

    类比：和别的哨兵聊天。它监听 tool/result，每当有工具结果就记下来，
    还能主动用 bus.emit 广播给其他哨兵（比如通知 Guard）。
    """

    seen_results: List[Dict[str, Any]] = field(default_factory=list)

    def mount(self, bus: EventBus, registry: Registry) -> Callable[[], None]:
        def on_tool_result(data: Dict[str, Any]) -> None:
            self.seen_results.append(data)
            # 主动与其他哨兵交流：广播一个"工具完成"事件
            bus.emit("tool/completed", {"count": len(self.seen_results)})

        return bus.on("tool/result", on_tool_result)


@dataclass
class WorkerSentry(Sentry):
    """懒激活哨兵（Worker）：平时待命，被显式调用才干活。

    类比：LLM —— 不被 agent 调用时它不做任何事，被 generate() 调才执行。
    这里用 registry 注册一个"sum"工具作为它的可调用能力，只有被工具调用
    时才真正计算。
    """

    invocations: int = 0

    def mount(self, bus: EventBus, registry: Registry) -> Callable[[], None]:
        # 懒哨兵不监听事件、不主动跑。它只是注册一个工具，供运行时调用。
        # 注册进 Registry，被调用时才执行核心逻辑。
        from .registry import ToolDefinition

        disposers: List[Callable[[], None]] = []

        def add_tool() -> None:
            tool = ToolDefinition(
                name="sum",
                description="Add two numbers",
                parameters={"type": "object", "properties": {
                    "a": {"type": "number"}, "b": {"type": "number"}}},
                backend="calc",
            )
            disposers.append(registry.register_tool(tool))

        # 需要一个计算后端；这里用闭包模拟一个后端（演示懒激活，非沙箱范围）
        from .security.capability import CapabilityBackend, ExecutionResult
        from .security.policy import ExecutionRequest, SandboxPolicy

        class CalcBackend(CapabilityBackend):
            name = "calc"

            async def execute(self, request: ExecutionRequest, policy: SandboxPolicy) -> ExecutionResult:
                self_worker.invocations += 1
                args = request.arguments
                a = args.get("a", 0)
                b = args.get("b", 0)
                return ExecutionResult(ok=True, value={"sum": a + b})

        self_worker = self
        calc_backend = CalcBackend()
        disposers.append(registry.register_backend("calc", calc_backend))
        add_tool()

        def unload() -> None:
            for d in reversed(disposers):
                d()

        return unload
