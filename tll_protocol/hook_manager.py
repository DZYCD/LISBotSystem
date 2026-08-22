"""
Hook 统一管理模块 - LIS v2 TLL 协议

所有 hook（无论从何处触发）都必须经过此模块才能执行。
职责：
1. 统一分发：将日志事件按级别路由到对应 hook 函数
2. 全量捕获：记录每一次 hook 调用的元数据（时间、级别、任务、hook 名、结果）
3. 实时监控：维护最近事件环形队列，供前端大屏展示
4. 任务追踪：每次触发都携带 TASK 基础信息，便于迭代追踪
5. 支持通过 result_holder 让 hook 返回结果给调用方

用法：
    from hook_manager import hook_manager
    hook_manager.register_hook('error', error_hook)
    hook_manager.dispatch('error', 'msg', logger, task)
"""

import time
import uuid
import json
from collections import deque
from typing import Callable, Dict, Optional, Any


class HookEvent:
    """一次 hook 调用的完整记录"""

    def __init__(self, hook_name: str, level: str, task_id: str, message: str,
                 timestamp: float, status: str, detail: str, task_info: Optional[Dict] = None):
        self.hook_name = hook_name
        self.level = level
        self.task_id = task_id
        self.message = message
        self.timestamp = timestamp
        self.status = status   # success / error / skipped / fallback
        self.detail = detail
        self.task_info = task_info or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hook_name': self.hook_name,
            'level': self.level,
            'task_id': self.task_id,
            'message': self.message,
            'timestamp': self.timestamp,
            'status': self.status,
            'detail': self.detail,
            'task_info': self.task_info
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


def _extract_task_info(task) -> Dict[str, Any]:
    """从 Task 对象提取基础信息，用于监控与追踪"""
    if task is None:
        return {}
    tll = getattr(task, 'tlljson', None)
    return {
        'task_id': getattr(task, 'id', None),
        'type': getattr(task, 'type', None),
        'status': getattr(task, 'status', None).value if getattr(task, 'status', None) is not None else None,
        'from': getattr(task, 'from_bot', None),
        'current_agent': getattr(task, 'current_agent', None),
        'forward_target': getattr(task, 'forward_target', None),
        'tll_from': getattr(tll, 'from_bot', None) if tll else None,
        'tll_command': getattr(tll, 'command', None) if tll else None,
        'tll_to': getattr(tll, 'to', None) if tll else None,
        'tll_task_func': getattr(tll, 'task_func', None) if tll else None
    }


class HookManager:
    """
    全局 Hook 调度器。维护各级别的 hook 注册表，并记录所有调用事件。
    """

    def __init__(self, max_events: int = 1000, node_id: str = ''):
        self._hooks: Dict[str, Callable] = {}
        self._events: deque = deque(maxlen=max_events)  # 环形事件队列
        self._enabled = True
        self.event_publisher = None  # 可选的事件发布器，用于将事件发送到统一主题
        self.node_id = node_id

    def register_hook(self, level: str, func: Callable):
        """注册某个级别的 hook 函数。"""
        self._hooks[level] = func

    def unregister_hook(self, level: str):
        """注销某个级别的 hook。"""
        self._hooks.pop(level, None)

    def dispatch(self, level: str, message: str, logger=None, task=None, **kwargs) -> Any:
        """
        统一分发入口。无论 hook 从何处触发，都经过这里。
        捕获所有调用，记录日志，并执行对应 hook。
        返回 hook 的返回值，若 hook 未注册则返回 None。
        kwargs 会透传给 hook 函数，例如 result_holder。
        """
        if not self._enabled:
            return None

        task_id = getattr(task, 'id', None) or getattr(getattr(logger, 'task', None), 'id', None) or 'unknown'
        task_info = _extract_task_info(task if task is not None else getattr(logger, 'task', None))
        hook = self._hooks.get(level)

        if hook is None:
            self._record(hook_name=f'none:{level}', level=level, task_id=task_id,
                        message=message, status='skipped', detail='未注册 hook', task_info=task_info)
            return None

        event_id = uuid.uuid4().hex
        try:
            # 执行前记录
            self._record(hook_name=f'{level}', level=level, task_id=task_id,
                        message=message, status='running', detail=event_id, task_info=task_info)
            h_start = time.time()
            result = hook(message, logger=logger, task=task, **kwargs)
            h_dur = time.time() - h_start
            self._record(hook_name=f'{level}:{event_id}', level=level, task_id=task_id,
                        message=message, status='success',
                        detail=f'耗时 {h_dur*1000:.2f} ms', task_info=task_info)
            return result
        except Exception as e:
            self._record(hook_name=f'{level}:{event_id}', level=level, task_id=task_id,
                        message=message, status='error', detail=str(e), task_info=task_info)
            # 如果 hook 执行失败，且调用方提供了 result_holder，则记录错误
            if 'result_holder' in kwargs and isinstance(kwargs['result_holder'], dict):
                kwargs['result_holder']['error'] = str(e)
            return None

    def _record(self, hook_name: str, level: str, task_id: str, message: str,
                status: str, detail: str, task_info: Optional[Dict] = None):
        """记录一次 hook 调用事件。"""
        event = HookEvent(
            hook_name=hook_name,
            level=level,
            task_id=task_id,
            message=message,
            timestamp=time.time(),
            status=status,
            detail=detail,
            task_info=task_info
        )
        self._events.append(event)
        # 仅发送最终状态（success/error/skipped），running 状态只记录不上送，避免重复
        if status != 'running' and self.event_publisher is not None:
            try:
                event_dict = event.to_dict()
                if self.node_id:
                    event_dict['source_bot'] = self.node_id
                self.event_publisher(event_dict)
            except Exception as e:
                pass

    def get_recent_events(self, limit: int = 50) -> list:
        """获取最近的 hook 调用记录（用于前端大屏监控）"""
        recent = list(self._events)[-limit:]
        return [e.to_dict() for e in recent]

    def get_stats(self) -> Dict[str, Any]:
        """汇总统计信息"""
        stats = {
            'total': len(self._events),
            'by_level': {},
            'by_status': {}
        }
        for e in self._events:
            stats['by_level'][e.level] = stats['by_level'].get(e.level, 0) + 1
            stats['by_status'][e.status] = stats['by_status'].get(e.status, 0) + 1
        return stats

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def set_event_publisher(self, publisher):
        """设置事件发布器，每次记录事件时调用 publisher(event_dict)"""
        self.event_publisher = publisher

    def record_external_event(self, event_dict: dict):
        """记录来自其他节点的事件（用于监控机器人汇聚全量 hook 数据）"""
        try:
            event = HookEvent(
                hook_name=event_dict.get('hook_name', ''),
                level=event_dict.get('level', 'info'),
                task_id=event_dict.get('task_id', ''),
                message=event_dict.get('message', ''),
                timestamp=event_dict.get('timestamp', time.time()),
                status=event_dict.get('status', 'unknown'),
                detail=event_dict.get('detail', ''),
                task_info=event_dict.get('task_info', {})
            )
            self._events.append(event)
        except Exception as e:
            pass


# 全局单例，供所有模块使用
hook_manager = HookManager()
