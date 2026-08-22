"""V2 上报/监控链路验证：skaye_sv + 若干 bot 全链路测试。

验证：
1. bot 启动上报 LISreport → skaye_sv 登记 registered_bots.json
2. list_eiar_robots 返回 EiAr 组
3. skaye_sv ping bot → bot 返回心跳
4. skaye_996 委托 skaye_sv 的 list_eiar_robots
"""

import asyncio
import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from tll_protocol_v2.core import Task, TaskStatus, TLLjson
from tll_protocol_v2.node import build_node_from_yaml
from tll_protocol_v2.report_sender import report_to_sv
from tll_protocol_v2.sv_tools import RegistryStore

BASE = Path(__file__).resolve().parents[1]
BOTS = BASE / "bots"
SKAYE_SV = "agent/skaye_sv"


async def main():
    print("=" * 62)
    print("V2 上报/监控链路验证")
    print("=" * 62)

    # 用临时目录避免污染真实 registered_bots.json
    tmp = tempfile.mkdtemp()
    print(f"测试用 registered_bots 目录: {tmp}")

    # 构造 skaye_sv node（禁用 skills 扫描和 tool_list，手动注册 V2 统一工具）
    sv_cfg = yaml.safe_load(open(BOTS / "skaye_sv/bot.yaml", encoding="utf-8"))
    sv = build_node_from_yaml(BOTS / "skaye_sv/bot.yaml", skills_dir="", tool_list_path="")
    # 覆盖 registered_bots 存储到临时目录
    store = RegistryStore(tmp)
    from tll_protocol_v2 import sv_tools
    sv.registry.register_backend("sv_record", sv_tools.create_record_lis_tool(store))
    _safe_reg(sv, _tdef("record_lis", "sv_record"))
    sv.registry.register_backend("sv_list", sv_tools.create_list_eiar_robots_tool(store))
    _safe_reg(sv, _tdef("list_eiar_robots", "sv_list"))
    sv.registry.register_backend("sv_ping", sv_tools.create_ping_tool(store, sv.config.bot_id))
    _safe_reg(sv, _tdef("ping", "sv_ping"))
    print(f"\n[Skaye_SV] {sv.config.bot_id} 装配监控工具")
    print(f"  已注册工具: {[t.name for t in sv.registry.list_tools()]}")

    # 构造 bot 节点（禁用 skills 扫描和 tool_list）
    eiar1 = build_node_from_yaml(BOTS / "eiar_001/bot.yaml", skills_dir="", tool_list_path="")
    sayi = build_node_from_yaml(BOTS / "sayi_996/bot.yaml", skills_dir="", tool_list_path="")
    # 注册 ping（心跳响应）
    for n in (eiar1, sayi):
        _register_bot_ping(n)
    print(f"[bots] {eiar1.config.bot_id}, {sayi.config.bot_id} 装配 ping 工具")

    # 连接
    for n in (sv, eiar1, sayi):
        if not await asyncio.to_thread(n.mqtt.connect, 15):
            print(f"{n.config.bot_id} 连接失败"); return 1
    print("\n[MQTT] 已连接 broker.emqx.io")

    loop = asyncio.get_running_loop()

    # skaye_sv 收消息：执行本地工具（record_lis 等）
    async def sv_handle(task):
        final = await sv.handle_new_task(task, None)
        task.output = final; task.result = final; task.status = TaskStatus.SUCCESS
        sv.mqtt.send_task(task, task.from_bot, f"tll/{task.from_bot}")
    def on_sv(data, topic):
        loop.call_soon_threadsafe(lambda: loop.create_task(sv_handle(Task.from_dict(data["task"]))))
    sv.mqtt.on_envelope = on_sv

    # bot 收消息：执行本地工具（ping 等）
    async def bot_handle(node, task):
        final = await node.handle_new_task(task, None)
        task.output = final; task.result = final; task.status = TaskStatus.SUCCESS
        node.mqtt.send_task(task, task.from_bot, f"tll/{task.from_bot}")
    eiar1.mqtt.on_envelope = lambda d, t: loop.call_soon_threadsafe(
        lambda: loop.create_task(bot_handle(eiar1, Task.from_dict(d["task"]))))
    sayi.mqtt.on_envelope = lambda d, t: loop.call_soon_threadsafe(
        lambda: loop.create_task(bot_handle(sayi, Task.from_dict(d["task"]))))

    # 1. bot 上报
    print("\n=== 1. bot 上报 LISreport ===")
    await report_to_sv(eiar1, _load_cfg(eiar1), SKAYE_SV)
    await report_to_sv(sayi, _load_cfg(sayi), SKAYE_SV)
    await asyncio.sleep(2)
    registered = store.all()
    print(f"registered_bots: {list(registered.keys())}")
    print(f"  eiar_001 上报内容: {json.dumps({k: registered.get('agent/eiar_001',{}).get(k) for k in ('group','name','tools','network')}, ensure_ascii=False)}")

    # 2. list_eiar_robots（模拟 skaye_996 委托）
    print("\n=== 2. skaye_996 委托 skaye_sv 的 list_eiar_robots ===")
    from lis_harness.registry import ToolCall
    r = await sv.tool_runtime.execute(ToolCall(name="list_eiar_robots", arguments={}, actor="agent/skaye_996"))
    eiar_robots = r.value.get("eiar_robots", [])
    print(f"EiAr 机器人: {[b['bot_id'] for b in eiar_robots]}")

    # 3. skaye_sv ping bot（心跳）
    print("\n=== 3. skaye_sv 向 bot 发 ping ===")
    for target in ("agent/eiar_001", "agent/sayi_996"):
        t = Task(task_type="general", from_bot=SKAYE_SV, current_agent=SKAYE_SV,
                 tlljson=TLLjson(from_bot=SKAYE_SV, command="ping", to=target, params={}))
        sv.mqtt.send_task(t, target, f"tll/{target}")
    await asyncio.sleep(3)
    print("ping 已发送")

    print("\n=== 验证完成 ===")
    for n in (sv, eiar1, sayi):
        n.mqtt.close()
    return 0


def _tdef(name, backend):
    from lis_harness.registry import ToolDefinition
    return ToolDefinition(name=name, description=name, parameters={"type": "object", "properties": {}}, backend=backend)


def _safe_reg(node, tdef):
    """已注册则跳过，否则注册。"""
    try:
        node.registry.get_tool(tdef.name)
        return  # 已存在
    except Exception:
        pass
    node.registry.register_tool(tdef)


def _load_cfg(node):
    bot_name = node.config.bot_id.split("/")[-1]
    return yaml.safe_load(open(BOTS / bot_name / "bot.yaml", encoding="utf-8"))


def _register_bot_ping(node):
    from lis_harness.registry import ToolDefinition
    class PingBackend:
        name = "local"
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            return ExecutionResult(ok=True, value={"pong": True, "bot": node.config.bot_id})
    node.registry.register_backend("ping", PingBackend())
    node.registry.register_tool(ToolDefinition(
        name="ping", description="心跳", parameters={"type": "object", "properties": {}}, backend="ping"))


if __name__ == "__main__":
    asyncio.run(main())
