#!/usr/bin/env python3
"""update_eiar_access 工具 - 动态更新 SaYi_996 可访问的 EiAr 机器人列表。

将传入的 EiAr 机器人信息写入 bot.yaml 的 peers 字段，
配合 Bot.reload() 热加载机制，使 SaYi_996 无需重启即可
在后续委托中直接调用这些机器人。
"""

import os
import copy
import yaml


def _find_bot_yaml(start_dir=None):
    """定位当前机器人的 bot.yaml，兼容目录或文件路径。"""
    if start_dir is None:
        start_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ('bot.yaml', 'main.yaml', 'config.yaml'):
        candidate = os.path.join(start_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _safe_get_source(params):
    """从 params 中提取 eiar_robots 列表，兼容多种字段名。"""
    if 'eiar_robots' in params and isinstance(params['eiar_robots'], list):
        return params['eiar_robots']
    if 'robots' in params and isinstance(params['robots'], list):
        return params['robots']
    if 'list' in params and isinstance(params['list'], list):
        return params['list']
    return None


def handle(params=None, bot=None, task=None):
    params = params or {}
    eiar_list = _safe_get_source(params)
    if eiar_list is None:
        return {'status': 'error', 'info': '缺少 eiar_robots 参数（应为机器人列表）'}

    # 定位 bot.yaml
    bot_yaml_path = None
    if bot is not None and getattr(bot, 'base_dir', None):
        bot_yaml_path = _find_bot_yaml(bot.base_dir)
    if bot_yaml_path is None:
        return {'status': 'error', 'info': '无法定位 bot.yaml'}

    try:
        with open(bot_yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        return {'status': 'error', 'info': f'读取 bot.yaml 失败: {e}'}

    peers = data.get('peers', {})
    if not isinstance(peers, dict):
        peers = {}

    # 先清除所有旧的 EiAr 条目（兼容 group=EiAr 和 bot_id 含 eiar 的旧格式）
    stale_ids = [bid for bid, pinfo in peers.items()
                 if isinstance(pinfo, dict) and (pinfo.get('group') == 'EiAr' or 'eiar' in bid.lower())]
    for bid in stale_ids:
        del peers[bid]

    updated = []
    for item in eiar_list:
        if not isinstance(item, dict):
            continue
        bot_id = item.get('bot_id') or item.get('id')
        if not bot_id:
            continue
        # 规范化 peer 条目：仅保留 auth_key 与标准工具描述数组
        auth_key = item.get('auth_key') or ''
        tools_raw = item.get('tools', []) if isinstance(item.get('tools'), list) else []
        skills = item.get('skills', {}) if isinstance(item.get('skills'), dict) else {}
        tools = []
        for t in tools_raw:
            if isinstance(t, str):
                tdef = skills.get(t, {}) if isinstance(skills, dict) else {}
                entry = {'name': t}
                if isinstance(tdef, dict):
                    if tdef.get('description'):
                        entry['description'] = tdef['description']
                    if tdef.get('params'):
                        entry['params'] = tdef['params']
                    if tdef.get('access'):
                        entry['access'] = tdef['access']
                tools.append(entry)
            elif isinstance(t, dict):
                tools.append(t)
        peers[bot_id] = {'auth_key': auth_key, 'tools': tools}
        updated.append(bot_id)

    if not updated:
        return {'status': 'error', 'info': '传入的机器人列表中无有效 bot_id'}

    data['peers'] = peers

    # 原子写入：先写临时文件再替换，避免中途失败损坏配置
    tmp_path = bot_yaml_path + '.tmp'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, bot_yaml_path)
    except Exception as e:
        return {'status': 'error', 'info': f'写入 bot.yaml 失败: {e}'}

    # 立即触发热加载，使新配置生效
    if bot is not None and hasattr(bot, 'reload'):
        try:
            bot.reload()
        except Exception as e:
            return {'status': 'warning', 'info': f'已写入但热加载失败: {e}', 'updated': updated}

    return {'status': 'success', 'info': f'已更新 {len(updated)} 个 EiAr 机器人到 peers', 'updated': updated}
