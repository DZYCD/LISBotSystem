"""快速自检：验证 eiar_001 的 MQTT 连接 + DeepSeek key 是否有效。"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from pathlib import Path

BOT_YAML = Path(__file__).resolve().parents[1] / "bots" / "eiar_001" / "bot.yaml"

async def main():
    bot_cfg = yaml.safe_load(open(BOT_YAML, encoding="utf-8"))
    net = bot_cfg["networks"][0]

    # 1. MQTT 连接
    from tll_protocol_v2.mqtt import MQTTTransport, MQTTConfig
    m = MQTTTransport(MQTTConfig(host=net["url"], port=net["port"],
        topic=net["topic"], client_id=bot_cfg["id"], auth_key=bot_cfg["auth_key"]))
    ok = await asyncio.to_thread(m.connect, 15)
    print(f"[mqtt] 连接 {net['url']}:{net['port']} = {ok}")
    m.close()

    # 2. DeepSeek key 验证
    from lis_harness.adapters import DeepSeekClient
    from lis_harness.session import Message, TextBlock
    llm_cfg = bot_cfg.get("llm", {})
    try:
        client = DeepSeekClient(
            api_key=llm_cfg.get("api_key"),
            base_url=llm_cfg.get("base_url"),
            model=llm_cfg.get("model"),
        )
        resp = await client.generate([Message(role="user", content=[TextBlock(text="回复ok")])], tools=None)
        texts = [b.text for b in resp.blocks if hasattr(b, "text")]
        print(f"[llm] DeepSeek 响应: {texts}")
    except Exception as e:
        print(f"[llm] DeepSeek 失败: {e}")
    return 0

asyncio.run(main())
