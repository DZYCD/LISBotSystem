"""
TLL Logger Hooks 工厂目录

每个日志级别（error/warning/success/info/debug/finish）都有专属 hook。
还有 bot_create 等扩展 hook。
"""

from .error import error_hook
from .warning import warning_hook
from .success import success_hook
from .info import info_hook
from .debug import debug_hook
from .finish import finish_hook
from .bot_create import bot_create_hook

__all__ = [
    'error_hook',
    'warning_hook',
    'success_hook',
    'info_hook',
    'debug_hook',
    'finish_hook',
    'bot_create_hook'
]
