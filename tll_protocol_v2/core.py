"""TLL v2 核心数据结构 —— 保留旧 TLL 线协议契约。

这些结构与旧 tll_protocol/core.py 完全兼容，外部消费方（receiver、harness
适配器、monitor、archive 落盘）依赖这些键名。重写只改执行核心，不破坏线协议。
"""

from __future__ import annotations

import uuid
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(Enum):
    CREATED = 'created'
    PENDING = 'pending'
    RUNNING = 'running'
    SUCCESS = 'success'
    FAILED = 'failed'
    CHECK_REVIEW = 'check_review'
    RETURNING = 'returning'
    DELEGATED = 'delegated'


# 路由状态机关键状态
_DELEGATED = TaskStatus.DELEGATED
_RETURNING = TaskStatus.RETURNING
_CHECK_REVIEW = TaskStatus.CHECK_REVIEW


class TraceHop:
    def __init__(self, bot: str, action: str, timestamp: Optional[str] = None):
        self.bot = bot
        self.action = action
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {'bot': self.bot, 'action': self.action, 'timestamp': self.timestamp}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TraceHop":
        return cls(d.get('bot', ''), d.get('action', ''), d.get('timestamp'))


class Trace:
    def __init__(self, trace_id: Optional[str] = None):
        self.trace_id = trace_id or uuid.uuid4().hex
        self.hops: List[TraceHop] = []

    def add_hop(self, bot: str, action: str) -> None:
        self.hops.append(TraceHop(bot=bot, action=action))

    def to_dict(self) -> Dict[str, Any]:
        return {'trace_id': self.trace_id, 'hops': [h.to_dict() for h in self.hops]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Trace":
        t = cls(d.get('trace_id'))
        for h in d.get('hops', []) or []:
            t.hops.append(TraceHop.from_dict(h))
        return t


class TLLjson:
    def __init__(self, from_bot: str, command: str, to: str,
                 params: Optional[Dict] = None, task_func=None):
        self.from_bot = from_bot
        self.command = command
        self.to = to
        self.params = params or {}
        self.task_func = task_func  # 遗留字段，新路径恒 None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'from_bot': self.from_bot,
            'command': self.command,
            'to': self.to,
            'params': self.params,
            'task_func': self.task_func,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TLLjson":
        return cls(
            d.get('from_bot', ''),
            d.get('command', ''),
            d.get('to', ''),
            d.get('params', {}) or {},
            d.get('task_func'),
        )


class Task:
    """委托任务，线协议结构与旧 TLL 完全兼容。"""

    def __init__(self, task_type='general', from_bot='', current_agent='',
                 tlljson=None, task_id=None, prev_hop=None, sender_group=None):
        self.id = task_id or uuid.uuid4().hex
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.type = task_type
        self.status = TaskStatus.PENDING
        self.from_bot = from_bot
        self.current_agent = current_agent
        self.tlljson = tlljson
        self.prev_hop = prev_hop
        self.sender_group = sender_group
        self.output = None
        self.result = None
        self.error = None
        self.trace = Trace()
        self.forward_target = None
        self.logs: List[str] = []
        self.route: List[str] = []       # LIFO 委托栈
        self.delegate_count = 0
        self.last_target = ''
        self.original_text = None
        self.reviewed = False
        self.delegated = False
        self.local_executed = False      # 不序列化

    # --- 状态 ---

    def set_success(self, output):
        self._set_status(TaskStatus.SUCCESS)
        self.output = output
        self.result = output

    def set_failed(self, error):
        self._set_status(TaskStatus.FAILED)
        self.error = error
        self.result = error

    def _set_status(self, new_status):
        if self.status == new_status:
            return
        self.status = new_status

    # --- 序列化 ---

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'created_at': self.created_at,
            'type': self.type,
            'status': self.status.value,
            'from_bot': self.from_bot,
            'current_agent': self.current_agent,
            'prev_hop': self.prev_hop,
            'sender_group': self.sender_group,
            'tlljson': self.tlljson.to_dict() if self.tlljson else None,
            'output': self.output,
            'result': self.result,
            'error': self.error,
            'trace': self.trace.to_dict(),
            'forward_target': self.forward_target,
            'logs': self.logs,
            'route': self.route,
            'delegate_count': self.delegate_count,
            'last_target': self.last_target,
            'original_text': self.original_text,
            'reviewed': self.reviewed,
            'delegated': self.delegated,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        tll = None
        if data.get('tlljson'):
            tll = TLLjson.from_dict(data['tlljson'])
        task = cls(
            task_type=data.get('type', 'general'),
            from_bot=data.get('from_bot', ''),
            current_agent=data.get('current_agent', ''),
            tlljson=tll,
            task_id=data.get('id'),
            prev_hop=data.get('prev_hop'),
            sender_group=data.get('sender_group'),
        )
        try:
            task.status = TaskStatus(data.get('status', 'pending'))
        except ValueError:
            task.status = TaskStatus.PENDING
        task.created_at = data.get('created_at', task.created_at)
        task.output = data.get('output')
        task.result = data.get('result')
        task.error = data.get('error')
        task.forward_target = data.get('forward_target')
        task.logs = data.get('logs', [])
        task.route = data.get('route', [])
        task.delegate_count = data.get('delegate_count', 0)
        task.last_target = data.get('last_target', '')
        task.original_text = data.get('original_text')
        task.reviewed = data.get('reviewed', False)
        task.delegated = data.get('delegated', False)
        if data.get('trace'):
            task.trace = Trace.from_dict(data['trace'])
        return task


def create_task(target: str, command: str = None, params: dict = None,
                from_bot: str = '', current_agent: str = '') -> Task:
    """构造一个委托任务（线协议兼容旧 create_task）。"""
    return Task(
        task_type='general',
        from_bot=from_bot,
        current_agent=current_agent,
        tlljson=TLLjson(from_bot=from_bot, command=command or '', to=target, params=params or {}),
    )
