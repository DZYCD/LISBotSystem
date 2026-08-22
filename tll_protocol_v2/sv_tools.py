"""Skaye_SV 专用工具：注册中心（record_lis）+ 监控（list_eiar_robots/grant_permission/ping）。

V2 统一模型下，这些是 Skaye_SV 节点的「网络接收型本地工具」：
- 其他 bot 启动时用 task_create 委托 skaye_sv 的 record_lis → 本模块登记到
  registered_bots.json（内存 + 文件持久化）。
- Skaye_996 可委托 list_eiar_robots 获取 EiAr 组机器人。
- skaye_sv 自动 ping 调度会委托其他 bot 的 ping。

存储：registered_bots.json（Skaye_SV 所在目录）。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional


class RegistryStore:
    """注册信息存储：内存 + registered_bots.json 持久化。"""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.path = os.path.join(base_dir, "registered_bots.json")
        self._lock = threading.Lock()
        self._bots: Dict[str, Dict] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._bots.update(data)
        except Exception:
            pass

    def _save(self) -> None:
        os.makedirs(self.base_dir, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._bots, f, ensure_ascii=False, indent=2)

    def register(self, bot_id: str, info: Dict) -> None:
        info.setdefault("last_handshake", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        with self._lock:
            self._bots[bot_id] = info
            self._save()

    def update_handshake(self, bot_id: str) -> bool:
        with self._lock:
            if bot_id not in self._bots:
                return False
            self._bots[bot_id]["last_handshake"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save()
            return True

    def all(self) -> Dict[str, Dict]:
        with self._lock:
            return dict(self._bots)

    def by_group(self, group: str) -> List[Dict]:
        return [v for v in self.all().values() if v.get("group") == group]

    def has(self, bot_id: str) -> bool:
        return bot_id in self._bots


def create_record_lis_tool(store: RegistryStore):
    """创建 record_lis 工具后端（接收其他 bot 上报）。"""
    class RecordLisBackend:
        name = "sv"
        def __init__(self, store): self._store = store
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            info = request.arguments or {}
            bot_id = info.get("bot_id") or request.actor
            if not bot_id:
                return ExecutionResult(ok=False, error="record_lis: missing bot_id", denied=False)
            self._store.register(bot_id, info)
            total = len(self._store.all())
            return ExecutionResult(ok=True, value={
                "status": "success", "registered": bot_id, "total": total,
            })
    return RecordLisBackend(store)


def create_list_eiar_robots_tool(store: RegistryStore):
    """创建 list_eiar_robots 工具后端（返回 EiAr 组机器人）。"""
    class ListEiarBackend:
        name = "sv"
        def __init__(self, store): self._store = store
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            bots = self._store.by_group("EiAr")
            # 精简返回：bot_id / name / auth_key / network / tools / skills（含参数）
            result = []
            for b in bots:
                result.append({
                    "bot_id": b.get("bot_id"),
                    "name": b.get("name"),
                    "group": b.get("group"),
                    "auth_key": b.get("auth_key"),
                    "network": b.get("network", {}),
                    "tools": b.get("tools", []),
                    "skills": b.get("skills", {}),
                    "last_handshake": b.get("last_handshake"),
                })
            return ExecutionResult(ok=True, value={"eiar_robots": result})
    return ListEiarBackend(store)


def create_grant_permission_tool(store: RegistryStore):
    """创建 grant_permission 工具后端（授权）。"""
    class GrantBackend:
        name = "sv"
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            bot_id = request.arguments.get("bot_id")
            perm = request.arguments.get("permission", {})
            return ExecutionResult(ok=True, value={
                "status": "success", "granted_to": bot_id, "permission": perm,
            })
    return GrantBackend()


def create_ping_tool(store: RegistryStore, bot_id: str):
    """创建 ping 工具后端（返回注册信息，供其他 bot ping Skaye_SV）。"""
    class PingBackend:
        name = "sv"
        def __init__(self, store): self._store = store
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            info = self._store.all().get(bot_id, {})
            return ExecutionResult(ok=True, value={
                "pong": True, "bot": bot_id, "registered_info": info,
            })
    return PingBackend(store)


class TaskArchiveStore:
    """TASK 活动档集中存储：所有 bot 上报的 TASK 流转汇聚到 skaye_sv。

    存储：
    - {base_dir}/task_archive/{task_id}.json —— 每个 TASK 一份（含完整 trace 流转）
    - {base_dir}/events/events.jsonl —— 追加式事件流（运行事件）
    """

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.tasks_dir = os.path.join(base_dir, "task_archive")
        self.events_dir = os.path.join(base_dir, "events")
        os.makedirs(self.tasks_dir, exist_ok=True)
        os.makedirs(self.events_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._index: Dict[str, Dict] = {}  # task_id -> 摘要（快速列表）
        self._load_index()

    def _load_index(self) -> None:
        """启动时扫描已存档任务，重建摘要索引。"""
        try:
            for fn in os.listdir(self.tasks_dir):
                if not fn.endswith(".json"):
                    continue
                path = os.path.join(self.tasks_dir, fn)
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    self._index[data.get("task_id", fn[:-5])] = {
                        "task_id": data.get("task_id"),
                        "archived_at": data.get("archived_at"),
                        "from_bot": data.get("from_bot"),
                        "command": data.get("command"),
                        "status": data.get("status"),
                        "bot": data.get("bot"),
                    }
                except Exception:
                    continue
        except Exception:
            pass

    def add_task(self, task_id: str, data: Dict) -> None:
        """存一个 TASK 活动档。委托链多个 hop 上报同一 task_id 时合并（不互相覆盖）。

        合并策略：
        - logs（工具调用日志）：追加，标记每段来源 bot
        - trace：若已有且不同，追加（累积完整往返）
        - 其他字段：新数据优先覆盖（result/status 用最新）
        """
        with self._lock:
            path = os.path.join(self.tasks_dir, f"{task_id}.json")
            # 读取已有存档（若有）
            existing = {}
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = {}
            merged = dict(existing)
            # 合并工具调用日志（关键：聚合委托链所有 bot 的工具调用，去重）
            new_logs = list(data.get("logs") or [])
            if new_logs:
                bot = data.get("bot", "")
                existing_logs = list(existing.get("logs") or [])
                for lg in new_logs:
                    e = dict(lg)
                    e.setdefault("bot", bot)
                    dup = any(
                        e.get("bot") == el.get("bot")
                        and e.get("tool") == el.get("tool")
                        and e.get("event") == el.get("event")
                        and e.get("result") == el.get("result")
                        and e.get("args") == el.get("args")
                        for el in existing_logs
                    )
                    if not dup:
                        existing_logs.append(e)
                merged["logs"] = existing_logs
            # 合并 trace（累积每 hop，去重：相同 bot+action 只保留一次）
            new_trace = list(data.get("trace") or [])
            if new_trace:
                existing_trace = list(existing.get("trace") or [])
                for ht in new_trace:
                    dup = any(
                        ht.get("bot") == e.get("bot") and ht.get("action") == e.get("action")
                        for e in existing_trace
                    )
                    if not dup:
                        existing_trace.append(ht)
                merged["trace"] = existing_trace
            # 其他字段：新数据覆盖
            for k, v in data.items():
                if k in ("logs", "trace"):
                    continue
                merged[k] = v
            with open(path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2, default=str)
            self._index[task_id] = {
                "task_id": merged.get("task_id"),
                "archived_at": merged.get("archived_at"),
                "from_bot": merged.get("from_bot"),
                "command": merged.get("command"),
                "status": merged.get("status"),
                "bot": merged.get("bot"),
            }

    def add_event(self, event: Dict) -> None:
        """追加一条运行事件。"""
        with self._lock:
            path = os.path.join(self.events_dir, "events.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def get_task(self, task_id: str) -> Optional[Dict]:
        path = os.path.join(self.tasks_dir, f"{task_id}.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def recent_tasks(self, limit: int = 50) -> List[Dict]:
        items = list(self._index.values())
        items.sort(key=lambda x: x.get("archived_at", ""), reverse=True)
        return items[:limit]

    def recent_events(self, limit: int = 100) -> List[Dict]:
        path = os.path.join(self.events_dir, "events.jsonl")
        if not os.path.isfile(path):
            return []
        events = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
        events.sort(key=lambda x: x.get("ts", ""), reverse=True)
        return events[:limit]

    def all_tasks(self) -> List[Dict]:
        return list(self._index.values())


def create_task_archive_tool(store: TaskArchiveStore):
    """创建 task_archive 工具后端：接收 bot 上报的 TASK 活动档。"""
    class TaskArchiveBackend:
        name = "sv"
        def __init__(self, store): self._store = store
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            args = request.arguments or {}
            task_id = args.get("task_id") or (args.get("task") or {}).get("id")
            if not task_id:
                return ExecutionResult(ok=False, error="task_archive: missing task_id", denied=False)
            data = dict(args)
            data.setdefault("bot", request.actor)
            self._store.add_task(task_id, data)
            return ExecutionResult(ok=True, value={"status": "success", "task_id": task_id})
    return TaskArchiveBackend(store)


def create_event_report_tool(store: TaskArchiveStore):
    """创建 event_report 工具后端：接收 bot 上报的运行事件。"""
    class EventReportBackend:
        name = "sv"
        def __init__(self, store): self._store = store
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            args = request.arguments or {}
            from datetime import datetime
            from datetime import timezone
            event = dict(args)
            event.setdefault("ts", datetime.now(timezone.utc).isoformat())
            event.setdefault("bot_id", request.actor)
            self._store.add_event(event)
            return ExecutionResult(ok=True, value={"status": "success", "stored": True})
    return EventReportBackend(store)


def create_query_task_tool(store: TaskArchiveStore):
    """创建 query_task 工具后端：查 TASK 库（跨 bot 聚合，供大屏/LLM）。"""
    class QueryTaskBackend:
        name = "sv"
        def __init__(self, store): self._store = store
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            args = request.arguments or {}
            action = args.get("action", "tasks")
            if action == "task":
                tid = args.get("task_id", "")
                if not tid:
                    return ExecutionResult(ok=False, error="query_task: need task_id", denied=False)
                return ExecutionResult(ok=True, value={"task": self._store.get_task(tid)})
            if action == "events":
                return ExecutionResult(ok=True, value={"events": self._store.recent_events(int(args.get("limit", 100)))})
            limit = int(args.get("limit", 50))
            return ExecutionResult(ok=True, value={"tasks": self._store.recent_tasks(limit)})
    return QueryTaskBackend(store)
