"""
skaye_996 自定义 TASK Hook

每个机器人可以在这里定义自己的成功、错误等事件处理逻辑。
当前示例复用默认实现，但您可以按需覆盖。
"""

from tll_protocol.hooks import error_hook, warning_hook, success_hook, info_hook, debug_hook


def error(message, logger=None, task=None, **kwargs):
    print(f"[skaye_996] 错误: {message}")
    error_hook(message, logger=logger, task=task, **kwargs)


def warning(message, logger=None, task=None, **kwargs):
    warning_hook(message, logger=logger, task=task, **kwargs)


def success(message, logger=None, task=None, **kwargs):
    print(f"[skaye_996] 成功: {message}")
    success_hook(message, logger=logger, task=task, **kwargs)


def info(message, logger=None, task=None, **kwargs):
    info_hook(message, logger=logger, task=task, **kwargs)


def debug(message, logger=None, task=None, **kwargs):
    debug_hook(message, logger=logger, task=task, **kwargs)
