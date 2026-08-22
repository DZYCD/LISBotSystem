"""TLL v2 存档模块 —— 日志落盘。

两类存档（本质都是日志）：
1. TASK 活动档：记录每个 TASK 的网络流转（trace 每跳 / command / params / 结果）。
   落盘到 {base_dir}/tasks/{task_id}.json（单任务一份，完成后归档）。
2. BOT 运行档：记录本机运行轨迹（收到的任务、调用的工具、结果、错误）。
   落盘到 {base_dir}/run_log/run.jsonl（追加式，按 bot 归目录）。

复用旧 TLL 的 archive 思路（{task_id}.json），但适配 v2 结构（Task.to_dict）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


# --- TASK 活动档 ---

def archive_task(task, base_dir: str, note: str = "") -> Optional[str]:
    """把单个 TASK 的网络流转活动档落盘为 {task_id}.json。

    Args:
        task: v2 Task 对象（有 id/trace/logs/to_dict）。
        base_dir: bot 的 base_dir（落盘到 {base_dir}/tasks/）。
        note: 额外说明（可选，如 "回传完成"）。

    Returns:
        落盘文件路径；失败返回 None。
    """
    try:
        tasks_dir = os.path.join(base_dir, "tasks")
        _mkdir(tasks_dir)
        task_id = getattr(task, "id", None) or "unknown"
        path = os.path.join(tasks_dir, f"{task_id}.json")
        # trace 每跳（网络流转活动档的核心）
        trace = []
        if getattr(task, "trace", None):
            trace = [h.to_dict() for h in task.trace.hops]
        data = {
            "archived_at": _now(),
            "note": note,
            "task_id": task_id,
            "from_bot": getattr(task, "from_bot", ""),
            "current_agent": getattr(task, "current_agent", ""),
            "command": getattr(getattr(task, "tlljson", None), "command", ""),
            "params": getattr(getattr(task, "tlljson", None), "params", {}),
            "status": _status_value(getattr(task, "status", "")),
            "result": getattr(task, "output", None),
            "trace": trace,
            "logs": list(getattr(task, "logs", []) or []),
            "task": task.to_dict() if hasattr(task, "to_dict") else None,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)
        return path
    except Exception as e:  # 存档失败不影响主流程
        print(f"[archive] task 存档失败 {getattr(task, 'id', '?')}: {e}")
        return None


def _status_value(st) -> str:
    """TaskStatus enum → 字符串；否则原样。"""
    return getattr(st, "value", st) if st else ""


def _json_default(o):
    """json.dump 兜底：enum 转 value，datetime 转 iso。"""
    if hasattr(o, "value"):
        return o.value
    try:
        return o.isoformat()
    except Exception:
        return str(o)


# --- BOT 运行档 ---

def append_bot_log(base_dir: str, bot_id: str, entry: Dict[str, Any]) -> Optional[str]:
    """把本机一条运行日志追加到 {base_dir}/run_log/run.jsonl。

    Args:
        base_dir: bot 的 base_dir。
        bot_id: 本机机器人 id（日志归属）。
        entry: 一条日志（{event, detail, ...}）。会自动补时间戳与 bot_id。

    Returns:
        落盘文件路径；失败返回 None。
    """
    try:
        run_dir = os.path.join(base_dir, "run_log")
        _mkdir(run_dir)
        path = os.path.join(run_dir, "run.jsonl")
        record = {"ts": _now(), "bot_id": bot_id}
        record.update(entry or {})
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return path
    except Exception as e:
        print(f"[archive] bot 运行日志写入失败 {bot_id}: {e}")
        return None


def read_task_archive(base_dir: str, task_id: str) -> Optional[Dict[str, Any]]:
    """读取一个已归档的 TASK 活动档（返回 dict）。"""
    path = os.path.join(base_dir, "tasks", f"{task_id}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_bot_run_log(base_dir: str, max_lines: int = -1) -> list:
    """读取本机运行档（run.jsonl）。max_lines<0 读全部。"""
    path = os.path.join(base_dir, "run_log", "run.jsonl")
    if not os.path.isfile(path):
        return []
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except Exception:
                continue
    if max_lines > 0:
        lines = lines[-max_lines:]
    return lines
