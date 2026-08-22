# -*- coding: utf-8 -*-
# 动态权限更新工具 - 仅允许 SaYi_SV 调用
# 功能：修改指定机器人的 bot.yaml，将其 permissions.accept_from 添加新的 bot id。

import os
import yaml

def handle(params=None, task=None, bot=None, **kwargs):
    # 权限校验
    from_bot = getattr(task, 'from_bot', '') if task else ''
    if from_bot != 'agent/sayi_sv':
        return {'status': 'error', 'info': '权限不足，仅 SaYi_SV 可调用'}

    params = params or {}
    target_bot = params.get('target_bot', '')
    new_bot = params.get('new_bot', 'agent/sayi_sv')
    if not target_bot:
        return {'status': 'error', 'info': '缺少 target_bot'}

    # 定位目标机器人目录
    base = os.path.dirname(bot.base_dir)
    bot_name = target_bot.split('/')[-1]
    bot_dir = os.path.join(base, bot_name)
    if not os.path.isdir(bot_dir):
        return {'status': 'error', 'info': f'机器人 {target_bot} 不存在'}

    yaml_path = os.path.join(bot_dir, 'bot.yaml')
    if not os.path.isfile(yaml_path):
        return {'status': 'error', 'info': 'bot.yaml 不存在'}

    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

    permissions = data.setdefault('permissions', {})
    accept_from = permissions.setdefault('accept_from', [])
    if not isinstance(accept_from, list):
        accept_from = []
        permissions['accept_from'] = accept_from
    if new_bot not in accept_from:
        accept_from.append(new_bot)

    with open(yaml_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)

    return {'status': 'success', 'info': f'已为 {target_bot} 添加许可 {new_bot}'}