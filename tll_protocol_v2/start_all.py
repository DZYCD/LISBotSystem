"""一键启动脚本：启动所有 TLL v2 机器人 + 监控大屏。

顺序：
1. skaye_sv（注册中心，必须先启动，bot 才能上报）
2. 其他 bot（sayi_sv / sayi_996 / skaye_996 / eiar_001 / eiar_002）
3. 监控大屏（dashboard_v2.py，含以 sayi_sv 名义发任务能力）

用法：
    python start_all.py             # 启动全部
    python start_all.py --bots-only # 只启动机器人，不启动大屏
    python start_all.py --stop      # 停止已启动的全部

每个组件独立进程，Ctrl+C 或 --stop 全部停止。
"""

from __future__ import annotations

import os
import sys
import time
import signal
import subprocess
from pathlib import Path

DIR = Path(__file__).resolve().parent
LOG_DIR = DIR.parent / "debug_logs"   # LIS_v2/debug_logs

# 启动顺序：skaye_sv 必须先启动（注册中心）
BOT_ORDER = ["skaye_sv", "sayi_sv", "sayi_996", "skaye_996", "eiar_001", "eiar_002"]
DASHBOARD_PORT = 8080


def _python() -> str:
    return sys.executable


def _log_file(name: str):
    """返回该组件的日志文件路径（写入句柄 + 路径）。"""
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"{name}.log"
    fh = open(path, "a", encoding="utf-8", buffering=1)  # 行缓冲，实时可读
    return fh, path


def start_bot(bot_id: str) -> subprocess.Popen:
    """启动一个 bot 进程（start_v2.py <bot_id>），日志写入 debug_logs/{bot_id}.log。"""
    cmd = [_python(), "-u", str(DIR / "start_v2.py"), bot_id]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    fh, path = _log_file(bot_id)
    p = subprocess.Popen(cmd, env=env, stdout=fh, stderr=subprocess.STDOUT)
    print(f"[start] {bot_id} (pid={p.pid}, 日志: {path.relative_to(LOG_DIR.parent)})")
    return p


def start_dashboard() -> subprocess.Popen:
    """启动监控大屏（dashboard_v2.py <port>），日志写入 debug_logs/dashboard.log。"""
    cmd = [_python(), "-u", str(DIR / "dashboard_v2.py"), str(DASHBOARD_PORT)]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    fh, path = _log_file("dashboard")
    p = subprocess.Popen(cmd, env=env, stdout=fh, stderr=subprocess.STDOUT)
    print(f"[start] dashboard (pid={p.pid}, 日志: {path.relative_to(LOG_DIR.parent)})")
    return p


def tail_log(name: str):
    """跟随查看某组件的实时日志（Ctrl+C 退出）。"""
    path = LOG_DIR / f"{name}.log"
    if not path.is_file():
        print(f"无日志文件: {path}"); return 1
    print(f"跟随日志: {path}  (Ctrl+C 退出)")
    try:
        with open(path, encoding="utf-8") as f:
            # 跳到文件末尾（只看新增）
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    print(line, end="")
                else:
                    time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n退出日志查看")
    return 0


def stop_all(procs):
    """停止所有已启动的进程。"""
    for name, p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    # 等待退出，超时则强杀
    deadline = time.time() + 5
    for name, p in procs:
        while p.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
        print(f"[stop] {name}")


def main():
    args = sys.argv[1:]
    bots_only = "--bots-only" in args
    stop = "--stop" in args

    # --tail <bot>：跟随查看某组件实时日志
    if "--tail" in args:
        idx = args.index("--tail")
        if idx + 1 < len(args):
            return tail_log(args[idx + 1])
        print("用法: python start_all.py --tail <bot_id|dashboard>")
        return 1
    # --logs：列出所有日志文件
    if "--logs" in args:
        LOG_DIR.mkdir(exist_ok=True)
        print("日志文件 (debug_logs/):")
        for f in sorted(LOG_DIR.iterdir()):
            if f.is_file():
                print(f"  {f.name}  ({f.stat().st_size} bytes, {time.strftime('%H:%M:%S', time.localtime(f.stat().st_mtime))})")
        return 0

    if stop:
        # 由 start_all 自己管理进程，--stop 实际用 Ctrl+C 信号处理
        print("使用 Ctrl+C 停止全部进程。")
        return 0

    procs = []
    try:
        print("=" * 56)
        print("TLL v2 一键启动")
        print("=" * 56)
        for bot_id in BOT_ORDER:
            procs.append((bot_id, start_bot(bot_id)))
            time.sleep(0.5)  # 给每个 bot 一点启动间隔，避免并发冲击 broker

        if not bots_only:
            procs.append(("dashboard", start_dashboard()))

        print("\n全部已启动。Ctrl+C 停止所有进程。\n")
        # 监控子进程：若某个 bot 异常退出，打印提示
        while True:
            time.sleep(1)
            for name, p in procs:
                code = p.poll()
                if code is not None:
                    print(f"[warn] {name} 已退出 (code={code})")
    except KeyboardInterrupt:
        print("\n正在停止...")
    finally:
        stop_all(procs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
