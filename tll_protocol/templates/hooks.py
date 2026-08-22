'''
Bot 自定义 Hook 模板

提供默认实现，并包含可选的打印输出，便于追踪任务事件。
'''

from tll_protocol.hooks import error_hook, warning_hook, success_hook, info_hook, debug_hook


class BotHooks:
    def __init__(self, bot_name='bot', verbose=True):
        self.bot_name = bot_name
        self.verbose = verbose

    def _print(self, *args, **kwargs):
        if self.verbose:
            print(f'[{self.bot_name}]', *args, **kwargs)

    def error(self, message, logger=None, task=None, **kwargs):
        self._print(f'自定义错误处理: {message}')
        error_hook(message, logger=logger, task=task, **kwargs)

    def warning(self, message, logger=None, task=None, **kwargs):
        warning_hook(message, logger=logger, task=task, **kwargs)

    def success(self, message, logger=None, task=None, **kwargs):
        result = getattr(task, 'result', None) if task else None
        self._print(f'任务成功，收到 task.result = {result}')
        success_hook(message, logger=logger, task=task, **kwargs)

    def info(self, message, logger=None, task=None, **kwargs):
        info_hook(message, logger=logger, task=task, **kwargs)

    def debug(self, message, logger=None, task=None, **kwargs):
        debug_hook(message, logger=logger, task=task, **kwargs)
