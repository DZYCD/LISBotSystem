"""
全局调试日志重定向：将 print 同时输出到控制台和文件
在 MQTTTransport 初始化时调用 setup_debug_logging，即可捕获后续所有 print。
"""

import os
import sys
from datetime import datetime

_DEBUG_SETUP_DONE = False
_DEBUG_FILE = None

class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def setup_debug_logging(base_dir=None, name="debug"):
    global _DEBUG_SETUP_DONE, _DEBUG_FILE
    if _DEBUG_SETUP_DONE:
        return

    if base_dir is None:
        base_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "debug_logs"
        )
    os.makedirs(base_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = name.replace("/", "_").replace("\\", "_")
    log_path = os.path.join(base_dir, f"{safe_name}_{ts}.log")
    _DEBUG_FILE = open(log_path, "a", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, _DEBUG_FILE)
    _DEBUG_SETUP_DONE = True
    pass
