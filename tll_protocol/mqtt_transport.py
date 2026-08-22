"""
MQTT 传输模块 - LIS v2 TLL 协议

使用 paho-mqtt 实现 TASK 的收发。
"""

import json
import threading
import time
from datetime import datetime
import paho.mqtt.client as mqtt
from typing import Callable, Optional
from .debug_log import setup_debug_logging


class MQTTTransport:
    """基于 MQTT 的双向传输"""

    def __init__(self, host: str, port: int, topic: str, client_id: str,
                 on_message: Optional[Callable[[bytes], None]] = None,
                 additional_topics: Optional[list] = None):
        self.host = host
        self.port = port
        self.topic = topic
        self.client_id = client_id
        setup_debug_logging(name=client_id)
        self.on_message = on_message
        self.additional_topics = additional_topics or []
        self.client = mqtt.Client(client_id=client_id)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_subscribe = self._on_subscribe
        self.client.on_publish = self._on_publish
        self.connected = threading.Event()
        self._connected_once = False
        self._monitor_thread = None
        self._sub_topics = {}
        self._drop_count = 0
        self._reconnect_count = 0
        self._was_disconnected = False

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected.set()
            if self._connected_once:
                self._reconnect_count += 1
                self._was_disconnected = False
                pass
            else:
                pass
                self._connected_once = True
            # 订阅自己的 topic
            topics = []
            if self.topic:
                topics.append(self.topic)
            topics.extend(self.additional_topics)
            # 去重，避免重复订阅导致同一消息被投递多次
            topics = list(dict.fromkeys(topics))
            for t in topics:
                subscribe_result, mid = client.subscribe(t, qos=2)
                self._sub_topics[mid] = t
        else:
            pass

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        topic = self._sub_topics.get(mid, '?')
        if granted_qos and granted_qos[0] == 128:
            pass
        else:
            pass

    def _on_publish(self, client, userdata, mid):
        pass

    def _on_message(self, client, userdata, msg):
        pass
        if self.on_message:
            self.on_message(msg.payload)

    def connect(self, timeout=60):
        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()
        self.connected.wait(timeout=timeout)
        self._start_monitor()

    def _start_monitor(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _monitor_loop(self):
        while True:
            time.sleep(5)
            if not self.client.is_connected():
                if not self._was_disconnected:
                    self._was_disconnected = True
                    self._drop_count += 1
                    pass
                try:
                    self.client.reconnect()
                except Exception as e:
                    pass

    def send(self, data: bytes, target_topic: str = None):
        topic = target_topic or self.topic
        if not self.client.is_connected():
            pass
            return False
        info = self.client.publish(topic, data, qos=2)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            pass
            return False
        return True

    def get_connection_stats(self):
        """返回连接统计信息：断开次数、重连次数、当前连接状态"""
        return {
            'client_id': self.client_id,
            'connected': self.client.is_connected(),
            'drop_count': self._drop_count,
            'reconnect_count': self._reconnect_count,
            'host': self.host,
            'port': self.port
        }

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def send_once(data: bytes, host: str = '127.0.0.1', port: int = 1883, topic: str = 'tll/unknown', client_id: str = None) -> bool:
    """
    一次性发送：每次创建独立 MQTT 客户端，发送完即关闭，避免发送阻塞接收。
    """
    import uuid
    import time

    if client_id is None:
        client_id = f"tmp-{uuid.uuid4().hex[:12]}"

    c = mqtt.Client(client_id=client_id)
    try:
        c.connect(host, port, keepalive=30)
    except Exception as e:
        pass
        return False

    c.loop_start()
    info = c.publish(topic, data, qos=2)
    # 等待发布完成（最多 2 秒）
    deadline = time.time() + 2.0
    while not info.is_published() and time.time() < deadline:
        time.sleep(0.05)

    c.loop_stop()
    c.disconnect()
    if info.rc == mqtt.MQTT_ERR_SUCCESS:
        return True
    else:
        pass
        return False
