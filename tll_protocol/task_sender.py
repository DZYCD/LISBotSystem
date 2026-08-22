"""
TASK 发送模块 - LIS v2 TLL 协议

负责将 TASK 发送到目标机器人，支持路由记录和应用层加密。
"""

import time
import json
from typing import Optional
from datetime import datetime, timezone
from .core import Task, TLLjson
from .security import encrypt_payload


def _format_route(task, target):
    route = list(getattr(task, 'route', []))
    display = route + [target]
    return "[" + ", ".join(f"\033[33m{b}\033[0m" if b == target else str(b) for b in display) + "]"


def _default_topic_mapper(target: str) -> str:
    return f"tll/{target}"


class TaskSender:
    def __init__(self, transport=None, bot_id: str = "", topic_mapper=None, peers: Optional[dict] = None, group: str = None):
        """
        peers: {目标bot_id: {auth_key: "xxx"}}，用于获取目标机器人的密钥进行加密。
        group: 当前机器人的组名，用于发送任务时传递 sender_group。
        """
        self.transport = transport
        self.bot_id = bot_id
        self.topic_mapper = topic_mapper or _default_topic_mapper
        self.peers = peers or {}
        self.group = group or ''
    def send_task(self, task: Task, target: str, target_topic: Optional[str] = None, push_route: bool = True) -> bool:
        """发送 TASK，使用目标 bot 的 auth_key 加密 payload"""
        if self.transport is None:
            task.logger.error(f"No transport, cannot send task {task.id}")
            return False

        if push_route:
            task.route.append(self.bot_id)

        if push_route and self.group:
            task.sender_group = self.group

        payload = {
            "type": "TASK",
            "target": target,
            "sender": self.bot_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task": task.to_dict()
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        # 获取目标 bot 的 auth_key 进行加密
        target_key = ""
        if target in self.peers:
            target_key = self.peers[target].get('auth_key', '')

        if target_key:
            encrypted = encrypt_payload(data, target_key)
            final_data = json.dumps({
                "type": "ENCRYPTED_TASK",
                "target": target,
                "sender": self.bot_id,
                "timestamp": payload["timestamp"],
                "ciphertext": encrypted.decode('utf-8')
            }).encode('utf-8')
        else:
            final_data = data

        if target_topic is None:
            target_topic = self.topic_mapper(target)

        from .mqtt_transport import send_once

        def _do_send():
            if self.transport is None:
                return False
            host = getattr(self.transport, 'host', '127.0.0.1')
            port = getattr(self.transport, 'port', 1883)
            peer_info = self.peers.get(target, {})
            if peer_info.get('url'):
                host = peer_info['url']
            if peer_info.get('port'):
                port = int(peer_info['port'])
            topic = target_topic or self.topic_mapper(target)
            return send_once(final_data, host=host, port=port, topic=topic)

        send_ok = _do_send()
        if not send_ok:
            if task.logger is not None:
                task.logger.error(f"发送失败: target={target}, topic={target_topic}, push_route={push_route}")
            else:
                pass
            return False
        from .core import TaskStatus
        if push_route and task.status not in (TaskStatus.DELEGATED, TaskStatus.SUCCESS, TaskStatus.FAILED):
            task._set_status(TaskStatus.DELEGATED)
        cmd = task.tlljson.command if task.tlljson else ''
        params = task.tlljson.params if task.tlljson else {}
        params_str = json.dumps(params, ensure_ascii=False) if params else ''
        from .core import HIGHLIGHT, RESET
        if task.logger is not None:
            task.logger.context['bot_id'] = self.bot_id
            if push_route:
                task.logger.info(f"发射新委托 STATUS=DELEGATED 到 {HIGHLIGHT}{target}{RESET}，委托栈 {_format_route(task, target)}，命令 {HIGHLIGHT}{cmd}{RESET}，参数 {HIGHLIGHT}{params_str}{RESET}")
                pass
            else:
                task.logger.info(f"发射回传 STATUS=RETURNING 到 {HIGHLIGHT}{target}{RESET}，委托栈 {_format_route(task, target)}，命令 {HIGHLIGHT}{cmd}{RESET}，参数 {HIGHLIGHT}{params_str}{RESET}")
                pass
        else:
            pass
        return True

    def delegate(self, tlljson: TLLjson, task_type: str = "general", target_topic: Optional[str] = None) -> bool:
        task = Task(
            task_type=task_type,
            from_bot=tlljson.from_bot,
            current_agent=self.bot_id,
            tlljson=tlljson
        )
        return self.send_task(task, tlljson.to, target_topic=target_topic)
