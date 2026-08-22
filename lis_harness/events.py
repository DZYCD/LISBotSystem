"""轻量事件总线：插件（哨兵）之间通信的通道。

对应 dsh 的 ctx.on / 事件系统。哨兵通过 on() 订阅事件，bus.emit() 触发时
所有订阅者被调用。这实现了「事件驱动哨兵」——靠事件与其他插件交流，
而不是靠 LLM 调用。

事件类型示例：tool/call、tool/result、assistant/message、turn/start 等。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class EventBus:
    """一个事件发布/订阅中心。

    on() 返回 disposer（卸载订阅）—— 与 Registry 的插件卸载语义一致。
    """

    _handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = field(default_factory=dict)

    def on(self, event_type: str, handler: Callable[[Dict[str, Any]], None]) -> Callable[[], None]:
        """订阅一个事件类型，返回取消订阅的函数。"""
        handlers = self._handlers.setdefault(event_type, [])
        handlers.append(handler)
        disposed = [False]

        def disposer() -> None:
            if disposed[0]:
                return
            disposed[0] = True
            self._handlers[event_type].remove(handler)

        return disposer

    def emit(self, event_type: str, data: Dict[str, Any]) -> None:
        """触发一个事件，调用所有订阅者（含容错：单个订阅者异常不影响其他）。"""
        for handler in list(self._handlers.get(event_type, [])):
            try:
                handler(dict(data))
            except Exception:  # noqa: BLE001 - 哨兵异常不打断主流程
                pass

    def listeners(self, event_type: str) -> int:
        """当前某事件类型的订阅者数量（测试/内省用）。"""
        return len(self._handlers.get(event_type, []))
