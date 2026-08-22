"""
Bot 创建模块 - LIS v2 TLL 协议

外部脚本通过 request_bot_create() 请求创建 Bot，
实际创建逻辑通过 HookManager 触发 bot_create_hook 完成，
创建后自动加载 skills 工具。
"""

from .hook_manager import hook_manager
from .core import Logger
from .hooks.bot_create import bot_create_hook

# 确保 bot_create hook 已注册
if 'bot_create' not in hook_manager._hooks:
    hook_manager.register_hook('bot_create', bot_create_hook)


def request_bot_create(bot_path: str):
    """
    请求创建 Bot。
    Args:
        bot_path: bot 文件夹路径或 bot.yaml 文件路径
    Returns:
        Bot 实例
    Raises:
        RuntimeError: 如果创建失败
    """
    logger = Logger(task_type='bot_create')
    result_holder = {}
    hook_manager.dispatch('bot_create', bot_path, logger=logger, result_holder=result_holder)

    if 'bot' in result_holder:
        bot = result_holder['bot']
        # 设置 base_dir 并加载 skills
        if hasattr(bot, 'base_dir'):
            bot.base_dir = bot_path
        bot.register()
        return bot
    else:
        raise RuntimeError(result_holder.get('error', '未知错误'))
