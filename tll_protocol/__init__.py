"""TLL Protocol 核心包"""

from .core import (
    Task, TLLjson, Trace, TraceHop, Logger, TaskStatus,
    create_task, create_logger
)
from .input_module import TaskInputModule
from .task_sender import TaskSender
from .receiver import TaskReceiver
from .executor import TaskExecutor
from .hook_manager import HookManager, hook_manager, HookEvent
from .bot import Bot, BotConfig
from .bot_factory import request_bot_create

# 注册默认 hooks
from .hooks import (
    error_hook, warning_hook, success_hook, info_hook, debug_hook,
    bot_create_hook
)
hook_manager.register_hook('error', error_hook)
hook_manager.register_hook('warning', warning_hook)
hook_manager.register_hook('success', success_hook)
hook_manager.register_hook('info', info_hook)
hook_manager.register_hook('debug', debug_hook)
hook_manager.register_hook('bot_create', bot_create_hook)

__all__ = [
    'Task', 'TLLjson', 'Trace', 'TraceHop', 'Logger', 'TaskStatus',
    'create_task', 'create_logger',
    'TaskInputModule',
    'TaskSender',
    'TaskReceiver',
    'TaskExecutor',
    'HookManager', 'hook_manager', 'HookEvent',
    'Bot', 'BotConfig',
    'request_bot_create'
]
