#!/usr/bin/env python3
"""list_eiar_robots 工具 - 输出 EiAr 组机器人的通讯信息和工具列表。"""


def handle(params=None, task=None):
    try:
        from record_lis.tool import get_registered_bots
    except Exception:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from record_lis.tool import get_registered_bots

    bots = get_registered_bots()
    eiar_list = []
    for bot_id, info_dict in bots.items():
        if info_dict.get('group') == 'EiAr':
            eiar_list.append({
                'bot_id': bot_id,
                'name': info_dict.get('name', ''),
                'auth_key': info_dict.get('auth_key', ''),
                'network': info_dict.get('network', {}),
                'tools': info_dict.get('tools', []),
                'skills': info_dict.get('skills', {})
            })
    return {
        'status': 'success',
        'info': {
            'count': len(eiar_list),
            'eiar_robots': eiar_list
        }
    }
