#!/usr/bin/env python3
"""外部MQTT监控 - 订阅 tll/# 记录所有消息"""
import paho.mqtt.client as mqtt
from datetime import datetime
import os

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug_logs', 'monitor_output.log')

def on_connect(client, userdata, flags, rc):
    print(f"[MONITOR] connected rc={rc}, subscribing tll/#")
    client.subscribe("tll/#", qos=2)

def on_message(client, userdata, msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] topic={msg.topic}, qos={msg.qos}, payload_len={len(msg.payload)}, head={msg.payload[:200]}\n"
    print(line, end='')
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line)

def main():
    client = mqtt.Client(client_id="ext_monitor")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect("broker.emqx.io", 1883, keepalive=60)
    client.loop_forever()

if __name__ == "__main__":
    main()
