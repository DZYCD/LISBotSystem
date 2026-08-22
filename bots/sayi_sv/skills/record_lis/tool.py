#!/usr/bin/env python3
"""
record_lis 工具 - 接收其他机器人上报的通讯数据，登记到监控列表。
"""

import json
import os
import threading
from datetime import datetime

# 已注册机器人信息存储（内存 + 文件持久化）
_registered_bots = {}
_register_lock = threading.Lock()
_register_callbacks = []


def add_register_callback(callback):
    """注册回调函数，当有新机器人注册时调用（无参数）"""
    if callable(callback):
        _register_callbacks.append(callback)


def _load():
    """启动时从文件加载已注册机器人信息"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, 'registered_bots.json')
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _registered_bots.update(data)
            print(f"[record_lis] 已从文件恢复 {len(data)} 个机器人注册信息")
    except Exception as e:
        print(f"[record_lis] 加载注册信息失败: {e}")


def _save():
    """将注册信息保存到 json 文件，便于重启后恢复"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, 'registered_bots.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_registered_bots, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[record_lis] 保存注册信息失败: {e}")


def handle(params=None, task=None):
    """处理 record_lis 请求"""
    info = params or {}
    bot_id = info.get('bot_id') or (task.tlljson.from_bot if task and task.tlljson else None)
    if not bot_id:
        return {"status": "error", "info": "缺少 bot_id"}

    info.setdefault('last_handshake', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    with _register_lock:
        _registered_bots[bot_id] = info
        _save()

    for cb in _register_callbacks:
        try:
            cb()
        except Exception:
            pass

    print(f"[SaYi_SV] 已注册机器人: {bot_id} (总数: {len(_registered_bots)})")
    return {"status": "success", "info": f"已登记 {bot_id}"}


def get_registered_bots():
    """返回已注册机器人列表（每次重新从文件加载，确保多进程数据一致）"""
    with _register_lock:
        _load()
        _enhance_bots(_registered_bots)
        return dict(_registered_bots)


# 模块加载时恢复注册数据
_load()


def _enhance_bots(bots):
    """为注册数据补充 peer/auth_key/network 字段（从 bot.yaml 读取）"""
    try:
        import yaml
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
        bots_dir = os.path.join(base, 'bots')
        configs = {}
        if os.path.isdir(bots_dir):
            for bot_name in os.listdir(bots_dir):
                bot_dir = os.path.join(bots_dir, bot_name)
                yaml_path = os.path.join(bot_dir, 'bot.yaml')
                if os.path.isfile(yaml_path):
                    try:
                        with open(yaml_path, 'r', encoding='utf-8') as f:
                            cfg = yaml.safe_load(f)
                        bot_id = cfg.get('id')
                        if bot_id:
                            configs[bot_id] = cfg
                    except Exception:
                        pass
        for bot_id, data in bots.items():
            cfg = configs.get(bot_id)
            if not cfg:
                continue
            # 从 yaml 强制覆盖 group 和 name，避免旧注册数据错误
            if 'group' in cfg:
                data['group'] = cfg['group']
            if 'name' in cfg:
                data['name'] = cfg['name']
            if 'peer' not in data:
                peer = None
                my_parts = bot_id.split('/')[-1].split('_')
                prefix = my_parts[0].lower() if my_parts else ''
                suffix = my_parts[1] if len(my_parts) > 1 else None
                is_sv = suffix and suffix.lower() == 'sv'
                if not is_sv and prefix in ('sayi', 'skaye') and suffix:
                    target_prefix = 'skaye' if prefix == 'sayi' else 'sayi'
                    exact = f'agent/{target_prefix}_{suffix}'
                    peers = cfg.get('peers') or {}
                    if exact in peers:
                        data['peer'] = exact
            if 'auth_key' not in data:
                data['auth_key'] = cfg.get('auth_key', '')
            # 优先使用 yaml 配置的立绘路径，否则扫描 static 目录
            avatar_cfg = cfg.get('avatar', '')
            if avatar_cfg:
                data['avatars'] = [avatar_cfg]
            elif 'avatars' not in data or not data['avatars']:
                bot_dir = os.path.join(bots_dir, bot_id.split('/')[-1])
                static_dir = os.path.join(bot_dir, 'static')
                if os.path.isdir(static_dir):
                    avatars = [f for f in os.listdir(static_dir) if f.lower().endswith(('.png','.jpg','.jpeg','.gif'))]
                    if avatars:
                        data['avatars'] = avatars
            if 'network' not in data:
                network_info = {}
                for net in cfg.get('networks') or []:
                    if isinstance(net, dict) and net.get('network') == 'mqtt':
                        network_info = {
                            'url': net.get('url', ''),
                            'port': net.get('port', ''),
                            'topic': net.get('topic', '')
                        }
                        break
                if network_info:
                    data['network'] = network_info
            # 计算可联系列表（排除SV族）
            contacts = set()
            peers = cfg.get('peers') or {}
            for pid in peers.keys():
                if 'sv' not in pid.lower():
                    contacts.add(pid)
            fb = cfg.get('fallback')
            if fb and 'sv' not in fb.lower():
                contacts.add(fb)
            for other_id, other_cfg in configs.items():
                if other_id == bot_id or 'sv' in other_id.lower():
                    continue
                other_peers = other_cfg.get('peers') or {}
                if bot_id in other_peers or other_cfg.get('fallback') == bot_id:
                    contacts.add(other_id)
            data['contacts'] = [c for c in contacts if c in configs and 'sv' not in c.lower()]
    except Exception:
        pass


def update_last_handshake(bot_id, handshake_time=None):
    """更新指定机器人的上次握手时间，并触发大屏刷新"""
    if handshake_time is None:
        handshake_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _register_lock:
        if bot_id in _registered_bots:
            _registered_bots[bot_id]['last_handshake'] = handshake_time
            _save()
        else:
            return False
    for cb in _register_callbacks:
        try:
            cb()
        except Exception:
            pass
    print(f"[SaYi_SV] 已更新 {bot_id} 上次握手时间: {handshake_time}")
    return True
