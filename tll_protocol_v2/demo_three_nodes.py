"""三级委托演示：sayi_sv → sayi_996 → eiar_001 查文件内容。

所有配置从各机器人的 bot.yaml 读取（build_node_from_yaml）：
- 分层提示词 system_layers、peers、mqtt、auth_key 都来自 bot.yaml
- eiar_001 的本地工具（file_read 等）来自 tool_list 指向的工具清单 yaml
demo 只负责创建测试文件、发起任务、展示链路，不硬编码提示词/工具。
"""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tll_protocol_v2.core import Task, TaskStatus, TLLjson
from tll_protocol_v2.node import Node, build_node_from_yaml

from lis_harness.adapters import DeepSeekClient

BASE = Path(__file__).resolve().parents[1]
BOTS = BASE / "bots"


def build_llm(bot_cfg):
    llm = bot_cfg["llm"]
    return DeepSeekClient(api_key=llm["api_key"], base_url=llm["base_url"], model=llm["model"])


async def main():
    print("=" * 62)
    print("三级委托: sayi_sv → sayi_996 → eiar_001 查文件")
    print("=" * 62)

    # 测试文件（写在工作区，eiar_001 可读）
    test_file = Path(__file__).parent / "secret_info.txt"
    test_file.write_text("机密内容：LIS 集群的密码是 LIS-2026-MEMORY。", encoding="utf-8")

    # 从 bot.yaml 构造三个节点（所有配置来自配置文件）
    eiar = build_node_from_yaml(BOTS / "eiar_001" / "bot.yaml")
    sayi = build_node_from_yaml(BOTS / "sayi_996" / "bot.yaml")
    sv = build_node_from_yaml(BOTS / "sayi_sv" / "bot.yaml")

    print(f"\n[节点] {eiar.config.bot_id} (分层提示词+file_read工具)")
    print(f"[节点] {sayi.config.bot_id} (分层提示词，会委托 eiar_001)")
    print(f"[节点] {sv.config.bot_id} (无 LLM，仅创建任务)")

    # LLM
    import yaml
    eiar_cfg = yaml.safe_load(open(BOTS / "eiar_001" / "bot.yaml", encoding="utf-8"))
    sayi_cfg = yaml.safe_load(open(BOTS / "sayi_996" / "bot.yaml", encoding="utf-8"))
    eiar_llm = build_llm(eiar_cfg)
    sayi_llm = build_llm(sayi_cfg)

    # 连接
    for node in (eiar, sayi, sv):
        if not await asyncio.to_thread(node.mqtt.connect, 15):
            print(f"{node.config.bot_id} connect fail"); return 1
    print("[MQTT] 三节点已连接 broker.emqx.io", flush=True)

    loop = asyncio.get_running_loop()

    async def eiar_handle(task):
        print(f"[eiar] 收到委托 cmd={task.tlljson.command} from={task.from_bot}", flush=True)
        final = await eiar.handle_new_task(task, eiar_llm)
        task.output = final; task.result = final; task.status = TaskStatus.SUCCESS
        print(f"[eiar] 回传 -> {task.from_bot}: {final[:80]}", flush=True)
        eiar.mqtt.send_task(task, task.from_bot, f"tll/{task.from_bot}")
    def on_eiar(data, topic):
        loop.call_soon_threadsafe(lambda: loop.create_task(eiar_handle(Task.from_dict(data["task"]))))
    eiar.mqtt.on_envelope = on_eiar

    async def sayi_handle(task):
        print(f"[sayi_996] 收到委托 from={task.from_bot}: {task.tlljson.params.get('text','')[:40]}", flush=True)
        final = await sayi.handle_new_task(task, sayi_llm)
        task.output = final; task.result = final; task.status = TaskStatus.SUCCESS
        print(f"[sayi_996] 回传 -> {task.from_bot}:\n{final}", flush=True)
        sayi.mqtt.send_task(task, task.from_bot, f"tll/{task.from_bot}")
    def on_sayi(data, topic):
        loop.call_soon_threadsafe(lambda: loop.create_task(sayi_handle(Task.from_dict(data["task"]))))
    sayi.mqtt.on_envelope = on_sayi

    # SaYi_SV 创建任务，委托 sayi_996 问文件内容
    print("\n[SaYi_SV] 创建任务，委托 sayi_996 问文件内容...", flush=True)
    sv_task = Task(
        task_type="general", from_bot=sv.config.bot_id, current_agent=sv.config.bot_id,
        tlljson=TLLjson(from_bot=sv.config.bot_id, command="chat", to="agent/sayi_996",
                        params={"text": f"请查看文件 {test_file} 里有什么内容"}),
    )
    sv.mqtt.send_task(sv_task, "agent/sayi_996", f"tll/agent/sayi_996")
    print("[SaYi_SV] 已发送委托给 sayi_996\n", flush=True)

    await asyncio.sleep(45)
    print("\n[验证窗口结束]", flush=True)
    for node in (eiar, sayi, sv):
        node.mqtt.close()
    return 0


if __name__ == "__main__":
    asyncio.run(main())
