#!/usr/bin/env python3
"""
一键启动 LIS_v2 所有机器人

启动顺序：
1. sayi_996
2. skaye_996
3. eiar_001
4. eiar_002

按 Ctrl+C 退出，将终止所有子进程。
"""

import os
import sys
import subprocess
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
BOTS = ["skaye_sv", "sayi_996", "skaye_996", "eiar_001", "eiar_002"]


def main():
    procs = []
    try:
        for bot in BOTS:
            script = os.path.join(ROOT, "bots", bot, "start.py")
            print(f"[启动] {bot} ...")
            p = subprocess.Popen([sys.executable, script], cwd=ROOT)
            procs.append((bot, p))
            time.sleep(2)

        print("\n所有机器人已启动。按 Ctrl+C 停止。\n")

        while procs:
            for bot, p in procs:
                if p.poll() is not None:
                    print(f"[提示] {bot} 已退出，状态码={p.returncode}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止所有机器人...")
        for bot, p in procs:
            p.terminate()
        for bot, p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print("已全部停止。")


if __name__ == "__main__":
    main()
