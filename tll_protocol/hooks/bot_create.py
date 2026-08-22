"""
bot_create 级别 hook：从 YAML 文件创建 Bot 基础信息。
"""

import os
import yaml

from ..bot import Bot, BotConfig


def bot_create_hook(message, logger=None, task=None, **kwargs):
    """
    message: bot 文件夹路径或 bot.yaml 文件名。
    kwargs.result_holder: 如果提供，则创建结果写入其中。
    """
    result_holder = kwargs.get('result_holder')
    try:
        path = message
        if os.path.isdir(path):
            for fname in ('bot.yaml', 'main.yaml', 'config.yaml'):
                candidate = os.path.join(path, fname)
                if os.path.isfile(candidate):
                    path = candidate
                    break
            else:
                raise FileNotFoundError(f"在 {message} 中未找到 bot.yaml/main.yaml/config.yaml")

        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}

        # 提取自定义字段，避免传给 BotConfig 导致 TypeError
        avatar_path = data.pop('avatar', None)

        config = BotConfig(**data)
        bot = Bot(config, base_dir=os.path.dirname(path))
        if avatar_path is not None:
            bot.avatar = avatar_path
        bot.register()

        if result_holder is not None:
            result_holder['bot'] = bot
        if logger is not None:
            logger.success(f"Bot 创建成功: {bot.config.name} ({bot.config.id})")
    except Exception as e:
        if result_holder is not None:
            result_holder['error'] = str(e)
        if logger is not None:
            logger.error(f"Bot 创建失败: {e}")
