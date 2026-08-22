"""牵线链路验证：从 yaml 自动加载的接口工具能正确执行（写 bot.yaml peers）。

用真实 bot.yaml，但测试前后备份/恢复，不污染真实配置。
验证：set_sayi_contact（EiAr 侧）和 set_eiar_contacts（SaYi 侧）从 yaml 加载后可用。
"""

import asyncio
import sys
import os
import shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from tll_protocol_v2.node import build_node_from_yaml

BASE = Path(__file__).resolve().parents[1]
BOTS = BASE / "bots"

# 备份/恢复
BACKUPS = {}


def backup(bot_id):
    p = BOTS / bot_id / "bot.yaml"
    BACKUPS[bot_id] = p.read_text(encoding="utf-8")


def restore(bot_id):
    p = BOTS / bot_id / "bot.yaml"
    p.write_text(BACKUPS[bot_id], encoding="utf-8")


async def main():
    print("=" * 62)
    print("牵线链路：yaml 自动加载的接口工具验证")
    print("=" * 62)

    for bid in ("eiar_001", "sayi_996"):
        backup(bid)

    try:
        # 从真实 bot.yaml 构造 node（自动加载 tool_list 的接口工具）
        eiar = build_node_from_yaml(BOTS / "eiar_001/bot.yaml", skills_dir="")
        sayi = build_node_from_yaml(BOTS / "sayi_996/bot.yaml", skills_dir="")
        print(f"[nodes] {eiar.config.bot_id}, {sayi.config.bot_id}")
        print(f"  eiar 接口工具: {[t.name for t in eiar.registry.list_tools() if t.backend=='contact']}")
        print(f"  sayi 接口工具: {[t.name for t in sayi.registry.list_tools() if t.backend=='contact']}")

        # 连接
        for n in (eiar, sayi):
            if not await asyncio.to_thread(n.mqtt.connect, 15):
                print(f"{n.config.bot_id} 连接失败"); return 1
        print("[MQTT] 已连接 broker.emqx.io")

        loop = asyncio.get_running_loop()
        async def handle(node, task):
            print(f"[{node.config.bot_id}] 收到 {task.tlljson.command} from={task.from_bot}", flush=True)
            final = await node.handle_new_task(task, None)
            print(f"[{node.config.bot_id}] 结果: {str(final)[:90]}", flush=True)
            task.output = final; task.result = final; task.status = __import__("tll_protocol_v2.core", fromlist=["TaskStatus"]).TaskStatus.SUCCESS
            node.mqtt.send_task(task, task.from_bot, f"tll/{task.from_bot}")
        eiar.mqtt.on_envelope = lambda d, t: loop.call_soon_threadsafe(
            lambda: loop.create_task(handle(eiar, __import__("tll_protocol_v2.core", fromlist=["Task"]).Task.from_dict(d["task"]))))
        sayi.mqtt.on_envelope = lambda d, t: loop.call_soon_threadsafe(
            lambda: loop.create_task(handle(sayi, __import__("tll_protocol_v2.core", fromlist=["Task"]).Task.from_dict(d["task"]))))

        # 1. skaye_996 → eiar_001.set_sayi_contact
        print("\n=== 1. skaye_996 -> eiar_001.set_sayi_contact ===")
        from tll_protocol_v2.core import Task, TLLjson
        sayi_contact = {"bot_id": "agent/sayi_996", "auth_key": "sk-test",
                        "network": {"url": "broker.emqx.io", "port": 1883, "topic": "tll/agent/sayi_996"}}
        t = Task(task_type="general", from_bot="agent/skaye_996", current_agent="agent/skaye_996",
                 tlljson=TLLjson(from_bot="agent/skaye_996", command="set_sayi_contact", to="agent/eiar_001",
                                 params={"sayi_id": "agent/sayi_996", "sayi_info": sayi_contact}))
        eiar.mqtt.send_task(t, "agent/eiar_001", "tll/agent/eiar_001")
        await asyncio.sleep(2)
        data = yaml.safe_load(open(BOTS/"eiar_001/bot.yaml", encoding="utf-8"))
        peers = data.get("peers", {})
        print(f"eiar_001 peers: {list(peers.keys())}")
        print(f"  sayi_996 加入含工具? {'tools' in peers.get('agent/sayi_996', {})}")

        # 2. skaye_996 → sayi_996.set_eiar_contacts
        print("\n=== 2. skaye_996 -> sayi_996.set_eiar_contacts ===")
        eiar_list = [{"bot_id": "agent/eiar_001", "auth_key": "sk-eiar001",
                      "network": {"url": "broker.emqx.io", "port": 1883, "topic": "tll/agent/eiar_001"},
                      "tools": ["file_read", "file_write"]}]
        t2 = Task(task_type="general", from_bot="agent/skaye_996", current_agent="agent/skaye_996",
                  tlljson=TLLjson(from_bot="agent/skaye_996", command="set_eiar_contacts", to="agent/sayi_996",
                                  params={"eiar_list": eiar_list}))
        sayi.mqtt.send_task(t2, "agent/sayi_996", "tll/agent/sayi_996")
        await asyncio.sleep(2)
        data2 = yaml.safe_load(open(BOTS/"sayi_996/bot.yaml", encoding="utf-8"))
        peers2 = data2.get("peers", {})
        print(f"sayi_996 peers: {list(peers2.keys())}")
        if "agent/eiar_001" in peers2:
            print(f"  eiar_001 工具={peers2['agent/eiar_001'].get('tools')}")

        print("\n=== 验证完成 ===")
        for n in (eiar, sayi):
            n.mqtt.close()
    finally:
        # 恢复配置（不污染真实 bot.yaml）
        for bid in ("eiar_001", "sayi_996"):
            restore(bid)
        print("\n[已恢复原 bot.yaml]")
    return 0


if __name__ == "__main__":
    asyncio.run(main())
