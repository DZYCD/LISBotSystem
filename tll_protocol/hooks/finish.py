"""
FINISH hook - 默认实现：仅传递终结日志到监控，不自动归档。
自定义 hook 可返回 True 阻止销毁。
"""


def finish_hook(message, logger=None, task=None, **kwargs):
    return None
