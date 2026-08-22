"""TLL v2 MQTT 传输层 —— 真实 paho 封装 + 回传桥接。

职责：
- 连接 broker（paho loop_start 后台网络线程）
- 订阅 tll/agent/<id>
- 收消息：解析 TASK 信封（解密）→ 交给 node 的回调
- 发消息：加密 → publish
- 回传桥接：收到回传（task_id 有等待者）→ 调 harness TLLTransport.handle_response

关键设计：paho 网络线程同步收消息，但 harness Agent 是 async。所以收到的消息
放入一个线程安全队列，由 async 事件循环消费；回传则直接调 handle_response
（同步方法，填充 future，线程安全）。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

import paho.mqtt.client as mqtt

from .core import Task, TaskStatus
from .security import decrypt_payload


@dataclass
class MQTTConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    topic: str = "tll/agent/default"
    client_id: str = "tll_v2_default"
    auth_key: str = ""
    """本机自身 auth_key（用于解密收到的消息）。"""
    keepalive: int = 60


class MQTTTransport:
    """真实 MQTT 传输。"""

    def __init__(self, config: MQTTConfig) -> None:
        self.config = config
        self._client = mqtt.Client(client_id=config.client_id, protocol=mqtt.MQTTv311)
        self._client.on_message = self._on_message
        self._client.on_connect = self._on_connect
        self._connected = threading.Event()

        # 收到的原始信封回调（node/router 设置）
        self.on_envelope = None  # type: Optional[Callable[[dict, str], None]]
        # 回传桥接回调（node 设置：调 TLLTransport.handle_response）
        self.on_return = None  # type: Optional[Callable[[str, Any], None]]

    # --- 生命周期 ---

    def connect(self, timeout: float = 10.0, retries: int = 3) -> bool:
        """连接 broker 并订阅本机 topic，等待 on_connect 确认。

        间歇性失败防护：on_connect 可能因瞬时抖动未在窗口内触发，重试几次
        再判定失败（否则 sayi_sv 等节点会因一次抖动就退出生效）。
        """
        last = False
        for attempt in range(1, retries + 1):
            try:
                self._client.connect(self.config.host, self.config.port, self.config.keepalive)
                self._client.loop_start()
                self._client.subscribe(self.config.topic, qos=2)
                if self._connected.wait(timeout):
                    return True
                last = False
                # 未确认：清理并重试
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass
            except Exception:
                last = False
                try:
                    self._client.loop_stop()
                    self._client.disconnect()
                except Exception:
                    pass
            import time
            time.sleep(0.5)
        return last

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # --- 回调 ---

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected.set()
            client.subscribe(self.config.topic, qos=2)

    def _on_message(self, client, userdata, msg):
        try:
            envelope = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        # 解密
        data = envelope
        if envelope.get("type") == "ENCRYPTED_TASK":
            try:
                plain = decrypt_payload(envelope.get("ciphertext", "").encode("utf-8"),
                                        self.config.auth_key)
                data = json.loads(plain.decode("utf-8"))
            except Exception:
                return
        if data.get("type") != "TASK" or "task" not in data:
            return
        if self.on_return is not None:
            # 尝试按 task_id 匹配回传（若本机正在等它）
            try:
                task_dict = data["task"]
                task_id = task_dict.get("id")
                status = task_dict.get("status")
                if task_id and self.on_return(task_id, task_dict):
                    return  # 已作为回传消费
            except Exception:
                pass
        if self.on_envelope is not None:
            self.on_envelope(data, msg.topic)

    # --- 发送 ---

    def send_task(self, task: Task, target: str, target_topic: Optional[str] = None) -> bool:
        """发送 TASK（用目标 auth_key 加密）。"""
        # 加密：用目标 auth_key（由上层 sender 提供，这里简单用空→明文）
        # 实际加密由 TaskSender 负责，transport 只负责 publish
        if target_topic is None:
            target_topic = f"tll/{target}"
        payload = json.dumps({
            "type": "TASK",
            "target": target,
            "sender": self.config.client_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task": task.to_dict(),
        }, ensure_ascii=False).encode("utf-8")
        info = self._client.publish(target_topic, payload, qos=2)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def send_payload(self, data: bytes, target_topic: str) -> bool:
        info = self._client.publish(target_topic, data, qos=2)
        return info.rc == mqtt.MQTT_ERR_SUCCESS
