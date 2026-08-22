"""监控查询工具：让 LLM 能查询 v2 存档（机器人/任务/统计/运行档）。

复用 dashboard_v2 的数据聚合层，把查询结果封装成工具，供 LLM 调用。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from lis_harness.security.capability import ExecutionResult

from . import dashboard_v2


def _result(ok: bool, value=None, error="", denied=False):
    return ExecutionResult(ok=ok, value=value, error=error, denied=denied)


def _is_authorized(bot_id: str, allowed: Optional[list] = None) -> bool:
    """默认只允许 SV/Skaye 族查询（监管）。可传 allowed 覆盖。"""
    if allowed is None:
        return bool(bot_id) and (bot_id.startswith("agent/skaye")
                                 or bot_id.startswith("agent/sayi"))
    return bot_id in allowed


def create_dashboard_query_tool():
    """创建监控查询工具后端。查询存档/机器人/统计。"""
    class DashboardQuery:
        name = "dashboard"

        async def execute(self, request, policy):
            actor = request.actor
            if not _is_authorized(actor):
                return _result(False, error=f"dashboard_query: {actor} 无权查询", denied=True)
            args = request.arguments
            action = args.get("action", "stats")
            try:
                if action == "robots":
                    return _result(True, {"robots": dashboard_v2.get_registered_robots()})
                if action == "tasks":
                    limit = int(args.get("limit", 20))
                    return _result(True, {"tasks": dashboard_v2.get_all_task_archives(limit)})
                if action == "task":
                    tid = args.get("task_id", "")
                    if not tid:
                        return _result(False, error="dashboard_query: need task_id")
                    r = dashboard_v2.get_task_by_id(tid)
                    return _result(True, r if r else {"error": f"task {tid} not found"})
                if action == "bot_log":
                    bot = args.get("bot", "")
                    n = int(args.get("limit", 20))
                    if not bot:
                        return _result(False, error="dashboard_query: need bot")
                    return _result(True, {"bot": bot, "logs": dashboard_v2.get_bot_log(bot, n)})
                # 默认 stats
                return _result(True, {"stats": dashboard_v2.get_stats()})
            except Exception as e:
                return _result(False, error=f"dashboard_query failed: {e}")
    return DashboardQuery()
