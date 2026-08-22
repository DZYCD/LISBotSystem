"""
异步日志分发器 - LIS v2 TLL 协议

为每个机器人提供独立的日志发送线程，通过队列异步转发日志事件到监控端。
避免同步发送导致机器人主流程阻塞。

用法：
    dispatcher = AsyncLoggerDispatcher(bot_id='agent/xxx', publisher=send_func)
    dispatcher.start()
    dispatcher.enqueue(event_dict)
    dispatcher.stop()
"""

import queue
import threading
import time
from typing import Callable, Dict, Optional


class AsyncLoggerDispatcher:
    """每个机器人独立的异步日志发送器。"""

    def __init__(self, bot_id: str = '', publisher: Optional[Callable[[Dict], None]] = None,
                 max_queue_size: int = 5000, send_interval: float = 0.2):
        self.bot_id = bot_id
        self._publisher = publisher
        self._queue = queue.Queue(maxsize=max_queue_size)
        self._send_interval = send_interval
        self._thread = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name=f'log-dispatcher-{self.bot_id or "unknown"}',
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, event: Dict):
        """将事件放入发送队列，立即返回。若队列已满，丢弃新事件以保护主流程。"""
        if not self._running:
            return
        try:
            self._queue.put(event, block=False)
        except queue.Full:
            # 队列满时丢弃，避免阻塞主线程
            pass

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self):
        while self._running:
            try:
                # 等待下一条事件
                try:
                    event = self._queue.get(timeout=self._send_interval)
                except queue.Empty:
                    continue

                # 尝试顺带取出一条（减少循环开销）
                batch = [event]
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    pass

                if self._publisher is not None:
                    for evt in batch:
                        try:
                            self._publisher(evt)
                        except Exception:
                            # 发送失败忽略，避免后台线程崩溃
                            pass
            except Exception:
                # 异常保护，避免线程意外退出
                time.sleep(0.01)
