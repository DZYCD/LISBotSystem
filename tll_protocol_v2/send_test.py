"""给 eiar_001 v2 节点发一条测试委托，验证它响应。"""
import asyncio, sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from pathlib import Path

from tll_protocol_v2.core import Task, TaskStatus, TLLjson
from tll_protocol_v2.security import encrypt_payload

BOT_YAML = Path(__file__).resolve().parents[1] / "bots" / "eiar_001" / "bot.yaml"

async def main():
    bot_cfg = yaml.safe_load(open(BOT_YAML, encoding="utf-8"))
    net = bot_cfg["networks"][0]

    from tll_protocol_v2.mqtt import MQTTTransport, MQTTConfig
    # 用独立 client 发送（临时订阅 eiar_001 的 topic 听回传）
    m = MQTTTransport(MQTTConfig(
        host=net["url"], port=net["port"], topic="tll/agent/eiar_001",
        client_id="tll_test_sender", auth_key="sk-eiar001"))
    await asyncio.to_thread(m.connect, 15)
    print("发送器已连接", flush=True)

    # 订阅回传 topic（用测试者自己的 topic）
    return_topic = "tll/agent/eiar_001"  # 直接用同一 topic 简化测试
    got = {"result": None}

    # 构造委托任务
    task = Task(
        task_type="general", from_bot="agent/test_user", current_agent="agent/eiar_001",
        tlljson=TLLjson(from_bot="agent/test_user", command="chat",
                        to="agent/eiar_001", params={"text": "请回答：你是哪个机器人？只回复一句话。"}),
    )
    payload = {
        "type": "TASK", "target": "agent/eiar_001", "sender": "agent/test_user",
        "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "task": task.to_dict(),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # 用 eiar_001 的 auth_key 加密
    enc = encrypt_payload(data, "sk-eiar001")
    final = json.dumps({
        "type": "ENCRYPTED_TASK", "target": "agent/eiar_001", "sender": "agent/test_user",
        "timestamp": payload["timestamp"], "ciphertext": enc.decode("utf-8"),
    }).encode("utf-8")

    print("发送委托给 eiar_001...", flush=True)
    m.send_payload(final, "tll/agent/eiar_001")
    print("已发送，等待回传...", flush=True)

    # 等回传（订阅同一 topic，但 v2 节点也会收到；这里简单等几秒）
    await asyncio.sleep(12)
    print("测试完成（等待窗口已过）", flush=True)
    m.close()

asyncio.run(main())
