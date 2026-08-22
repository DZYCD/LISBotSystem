"""
TASK 接收模块 - LIS v2 TLL 协议

解析收到的字节流/dict，若为加密消息则用自身 auth_key 解密，还原 Task。
"""

import json
import time
from typing import Optional

from .core import Task
from .security import decrypt_payload


class TaskReceiver:
    def __init__(self, bot_id: str = '', auth_key: str = ''):
        self.bot_id = bot_id
        self.auth_key = auth_key

    def receive(self, data) -> Optional[Task]:
        """
        支持 bytes / str / dict，统一解析为 Task。
        返回 Task，失败返回 None。
        """
        try:
            if isinstance(data, bytes):
                data = data.decode('utf-8')
            if isinstance(data, str):
                payload = json.loads(data)
            else:
                payload = data

            if not payload:
                return None

            # 如果是加密消息，先解密
            if payload.get('type') == 'ENCRYPTED_TASK':
                ciphertext = payload.get('ciphertext', '')
                if not ciphertext:
                    return None
                try:
                    decrypted = decrypt_payload(ciphertext.encode('utf-8'), self.auth_key)
                    decrypted_str = decrypted.decode('utf-8')
                except Exception:
                    decrypted_str = ciphertext
                try:
                    payload = json.loads(decrypted_str)
                except Exception:
                    return None

            if payload.get('type') != 'TASK':
                return None

            task_data = payload.get('task')
            if not task_data:
                return None

            task = Task.from_dict(task_data)
            # 从消息头提取上一跳
            task.prev_hop = payload.get('sender', task.prev_hop)

            return task
        except Exception:
            return None

    def process(self, data) -> Optional[Task]:
        return self.receive(data)
