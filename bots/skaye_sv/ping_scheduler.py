#!/usr/bin/env python3
"""
Skaye_SV 定时 ping 调度模块（复用 trigger.send_ping）
"""

import threading
import time
import importlib.util
import os
from datetime import datetime

from tll_protocol.trigger import send_ping


def _load_record_lis_module():
    """动态加载 record_lis.tool，避免 sys.path 依赖"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(current_dir, 'skills', 'record_lis', 'tool.py')
    spec = importlib.util.spec_from_file_location('record_lis_tool', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ping_loop(bot):
    record_lis = _load_record_lis_module()
    get_registered_bots = record_lis.get_registered_bots
    while True:
        try:
            bots = get_registered_bots()
            bot_ids = list(bots.keys())
            if bot_ids:
                target = bot_ids[int(time.time() // 60) % len(bot_ids)]
                send_ping(bot, target)
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [ERROR] [Skaye_SV] ping 调度异常: {e}")
        time.sleep(600)


def start_ping_scheduler(bot):
    """启动定时 ping 线程（守护线程）"""
    t = threading.Thread(target=_ping_loop, args=(bot,), daemon=True)
    t.start()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [INFO] [Skaye_SV] 定时 ping 调度已启动（每 60 秒）")
    return t
