'''
sayi_996 自定义 TASK Hook

使用 tll_protocol.templates 中的 BotHooks 模板，保持默认实现与追踪打印。
'''

from tll_protocol.templates import BotHooks

hooks = BotHooks(bot_name='sayi_996')

error = hooks.error
warning = hooks.warning
success = hooks.success
info = hooks.info
debug = hooks.debug
