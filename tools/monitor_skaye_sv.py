#!/usr/bin/env python3
"""监控 tll/agent/skaye_sv 主题，打印所有消息原始内容"""
import json
import paho.mqtt.client as mqtt

BROKER = "broker.emqx.io"
PORT = 1883
TOPIC = "tll/agent/skaye_sv"

client = mqtt.Client(client_id="debug_monitor_skaye_sv")

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[已连接] {BROKER}:{PORT}")
        client.subscribe(TOPIC, qos=0)
        print(f"[已订阅] {TOPIC}")
    else:
        print(f"[连接失败] rc={rc}")

def on_message(client, userdata, msg):
    print(f"\n[{msg.timestamp}] 收到消息")
    print(f"  topic: {msg.topic}")
    print(f"  qos: {msg.qos}")
    print(f"  payload(前500): {msg.payload[:500]}")
    try:
        data = json.loads(msg.payload.decode('utf-8'))
        print(f"  type: {data.get('type')}")
        print(f"  target: {data.get('target')}")
        print(f"  sender: {data.get('sender')}")
        if data.get('task'):
            task = data['task']
            print(f"  task_id: {task.get('id')}")
            print(f"  tlljson: {json.dumps(task.get('tlljson'), ensure_ascii=False)}")
    except Exception as e:
        print(f"  [解析错误] {e}")

client.on_connect = on_connect
client.on_message = on_message
print(f"正在连接 {BROKER}:{PORT}...")
client.connect(BROKER, PORT, 60)
client.loop_forever()
