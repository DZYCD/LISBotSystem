import uuid
import traceback
import os
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


STATUS_COLORS = {
    'PENDING': '\033[34m',
    'RUNNING': '\033[35m',
    'SUCCESS': '\033[32m',
    'FAILED': '\033[31m',
    'CHECK_REVIEW': '\033[33m',
    'RETURNING': '\033[37m',
    'DELEGATED': '\033[38;5;208m',
    'RESET': '\033[0m'
}
HIGHLIGHT = '\033[1;33m'
RESET = '\033[0m'


class TraceHop:
    def __init__(self, bot: str, action: str, timestamp: Optional[str] = None):
        self.bot = bot
        self.action = action
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return {'bot': self.bot, 'action': self.action, 'timestamp': self.timestamp}


class Trace:
    def __init__(self, trace_id: Optional[str] = None):
        self.trace_id = trace_id or uuid.uuid4().hex
        self.hops: List[TraceHop] = []

    def add_hop(self, bot, action):
        self.hops.append(TraceHop(bot=bot, action=action))

    def to_dict(self):
        return {'trace_id': self.trace_id, 'hops': [h.to_dict() for h in self.hops]}


class TLLjson:
    def __init__(self, from_bot, command, to, params=None, task_func=None):
        self.from_bot = from_bot
        self.command = command
        self.to = to
        self.params = params or {}
        self.task_func = task_func

    def to_dict(self):
        return {'from_bot': self.from_bot, 'command': self.command, 'to': self.to, 'params': self.params, 'task_func': self.task_func}


class Logger:
    def __init__(self, task_type='general', task_id=None, hook_manager=None):
        self.task_id = task_id or uuid.uuid4().hex
        self.task_type = task_type
        self.buffer = []
        self.context = {}
        self.task = None
        self.hook_manager = hook_manager

    def log(self, level, message):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-1]
        # 优先从 task 对象获取当前机器人字段和任务 ID，保证每个 task 日志都带完整标识
        task_ref = self.task
        bot_id = self.context.get('bot_id') or getattr(task_ref, 'current_agent', None) or 'unknown'
        task_id = getattr(task_ref, 'id', None) or self.task_id or 'unknown'
        if level == 'error':
            _tb = traceback.format_exc().strip()
            if _tb and _tb != 'NoneType: None':
                message = f"{message}\nTraceback:\n{_tb}"
        entry = f"[{timestamp}] [{level}] [{bot_id}] [{task_id}] {message}"
        self.buffer.append(entry)
        if self.task is not None:
            self.task.logs.append(entry)
        # debug 级别暂时不输出到控制台/hook，仅记录
        if level == 'debug':
            return None
        # 优先使用 bot 的 hook_manager，否则回退到全局
        hook_manager = self.hook_manager or self.context.get('hook_manager')
        if hook_manager is not None:
            return hook_manager.dispatch(level, entry, logger=self, task=self.task)
        else:
            # 全局默认
            from .hook_manager import hook_manager as global_hook_manager
            return global_hook_manager.dispatch(level, entry, logger=self, task=self.task)

    def error(self, msg): return self.log('error', msg)
    def warning(self, msg): return self.log('warning', msg)
    def success(self, msg): return self.log('success', msg)
    def info(self, msg): return self.log('info', msg)
    def debug(self, msg): return self.log('debug', msg)
    def finish(self, msg): return self.log('finish', msg)

    def archive(self, task):
        from .archive import archive_task
        archive_dir = self.context.get('archive_dir')
        if archive_dir:
            archive_task(task, archive_dir)


class Task:
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
        self.logger = None
        self.logs = []
        self.route = []  # 委托路径，用于逐级回退
        self.delegate_count = 0  # 委托次数（每次向前委托+1，回退不变）
        self.command_queue = []  # 多命令队列（待委托的命令列表 [{target, command, params}]）
        self.queue_results = []  # 多命令队列已完成的子结果列表
        self.last_target = ''  # 当前子任务的目标机器人
        self.original_text = None  # 原始请求文本（跨节点保留，用于复核）
        self.reviewed = False  # 是否已执行最终复核
        self.delegated = False  # 是否对外委托过（委托方需要复核）
        self.local_executed = False  # 当前节点是否真正执行过 handler（不序列化）

    def set_success(self, output):
        self._set_status(TaskStatus.SUCCESS)
        self.output = output
        self.result = output

    def set_failed(self, error):
        self._set_status(TaskStatus.FAILED)
        self.error = error
        self.result = error

    def _set_status(self, new_status):
        old_status = self.status
        if old_status == new_status:
            return
        self.status = new_status
        if self.logger is not None:
            try:
                if new_status == TaskStatus.CREATED:
                    _cmd = getattr(getattr(self, 'tlljson', None), 'command', '')
                    _params = getattr(getattr(self, 'tlljson', None), 'params', {})
                    _detail = f" 命令={_cmd} 参数={json.dumps(_params, ensure_ascii=False)}" if _cmd else ''
                    self.logger.info(f"任务创建 STATUS=CREATED{_detail}")
                else:
                    from .core import STATUS_COLORS
                    _name = new_status.name
                    _color = STATUS_COLORS.get(_name, '')
                    _reset = STATUS_COLORS.get('RESET', '')
                    self.logger.info(f"状态转换：{old_status.name} -> {_color}{_name}{_reset}")
            except Exception:
                pass

    def to_dict(self):
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
            'delegated': self.delegated
        }

    @classmethod
    def from_dict(cls, data):
        tll = None
        if data.get('tlljson'):
            j = data['tlljson']
            tll = TLLjson(from_bot=j.get('from_bot',''), command=j.get('command',''), to=j.get('to',''), params=j.get('params',{}), task_func=j.get('task_func'))
        task = cls(
            task_type=data.get('type', 'general'),
            from_bot=data.get('from_bot', ''),
            current_agent=data.get('current_agent', ''),
            tlljson=tll,
            task_id=data.get('id'),
            prev_hop=data.get('prev_hop'),
            sender_group=data.get('sender_group')
        )
        task.status = TaskStatus(data.get('status', 'pending'))
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
            t = data['trace']
            task.trace = Trace(t.get('trace_id'))
            for hop in t.get('hops', []):
                task.trace.hops.append(TraceHop(bot=hop['bot'], action=hop['action'], timestamp=hop.get('timestamp')))
        return task


def create_task(task_type, from_bot, current_agent, tlljson=None, task_id=None, prev_hop=None):
    if isinstance(tlljson, dict):
        tlljson = TLLjson(**tlljson)
    task = Task(task_type=task_type, from_bot=from_bot, current_agent=current_agent, tlljson=tlljson, task_id=task_id, prev_hop=prev_hop)
    if tlljson:
        task.trace.add_hop(bot=from_bot, action='create')
    return task


def create_logger(task=None, task_type='general', hook_manager=None):
    logger = Logger(task_type=task_type, task_id=task.id if task else None, hook_manager=hook_manager)
    if task is not None:
        task.logger = logger
        logger.task = task
    return logger
