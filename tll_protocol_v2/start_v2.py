"""统一 V2 启动脚本：加载 bot.yaml → 装配 node → 上报 → 监听。

- skaye_sv：注册中心（record_lis/list_eiar_robots/grant_permission/ping）+ 自动 ping 调度
- 其他 bot：注册 ping 工具（心跳响应）+ 启动时 LISreport 上报给 skaye_sv
用法：
    python start_v2.py <bot_id>   # 如 python start_v2.py eiar_001
"""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from tll_protocol_v2.core import Task, TaskStatus, TLLjson
from tll_protocol_v2.node import Node, NodeConfig, build_node_from_yaml
from tll_protocol_v2.report_sender import report_to_sv

from lis_harness.adapters import DeepSeekClient
from lis_harness.registry import ToolDefinition

BASE = Path(__file__).resolve().parents[1]
BOTS = BASE / "bots"
SKAYE_SV = "agent/skaye_sv"


def build_llm(bot_cfg):
    llm = bot_cfg.get("llm", {})
    if not llm.get("enabled"):
        return None
    return DeepSeekClient(api_key=llm.get("api_key"), base_url=llm.get("base_url"),
                          model=llm.get("model") or "deepseek-chat",
                          timeout_ms=int(llm.get("timeout_ms", 120_000)))


def register_ping(node, bot_cfg):
    """给普通 bot 注册 ping 工具（心跳响应，返回注册信息）。"""
    from lis_harness.report import ToolReport
    from tll_protocol_v2.report_sender import build_registration_info
    class PingBackend:
        name = "local"
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            # 返回完整注册信息（网络/组别/可联系机器人/搭档/自身工具含参数）
            info = build_registration_info(node, bot_cfg)
            info["pong"] = True
            info["last_handshake"] = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return ExecutionResult(ok=True, value=info)
    node.registry.register_backend("ping", PingBackend())
    _safe_register_tool(node, ToolDefinition(
        name="ping", description="心跳检测，返回在线状态与完整工具/网络信息",
        parameters={"type": "object", "properties": {}}, backend="ping",
    ))


def _safe_register_tool(node, tdef):
    """工具已注册则跳过，否则注册。"""
    try:
        node.registry.get_tool(tdef.name)
        return
    except Exception:
        pass
    node.registry.register_tool(tdef)


def register_contact_tool(node, bot_yaml, bot_cfg):
    """按族注册牵线接口：EiAr→set_sayi_contact，SaYi→set_eiar_contacts。"""
    from tll_protocol_v2 import contact_tools
    from lis_harness.registry import ToolDefinition
    bot_id = bot_cfg.get("id", "")

    def on_change():
        # 写入后热重载：重建 node 的 tll peers（使新联系方式生效）
        try:
            node.tll._config.peers = {
                pid: {"tools": (p.get("tools") if isinstance(p, dict) else [])}
                for pid, p in node.config.peers.items()
            }
            # 也刷新 task_create 的 to enum
            for t in node.registry.list_tools():
                if t.name == "task_create":
                    peer_ids = list(node.config.peers.keys())
                    props = t.parameters.setdefault("properties", {})
                    if "to" in props and peer_ids:
                        props["to"] = {"type": "string", "enum": peer_ids}
                    break
            print(f"[contact] {bot_id} peers 已热重载: {list(node.config.peers.keys())}")
        except Exception as e:
            print(f"[contact] 热重载失败: {e}")

    if bot_id.startswith("agent/eiar"):
        node.registry.register_backend("contact", contact_tools.create_set_sayi_contact_tool(bot_yaml, on_change))
        _safe_register_tool(node, ToolDefinition(
            name="set_sayi_contact",
            description="请求 SaYi 接入（Skaye 族调用）：在 bot.yaml 添加 SaYi 联系方式（不含工具）",
            parameters={"type": "object", "properties": {
                "sayi_id": {"type": "string"},
                "sayi_info": {"type": "object"},
            }, "required": ["sayi_id"]},
            backend="contact",
        ))
    elif bot_id.startswith("agent/sayi"):
        node.registry.register_backend("contact", contact_tools.create_set_eiar_contacts_tool(bot_yaml, on_change))
        _safe_register_tool(node, ToolDefinition(
            name="set_eiar_contacts",
            description="请求联系 EiAr（Skaye 族调用）：在 bot.yaml 添加 EiAr 机器人和工具",
            parameters={"type": "object", "properties": {
                "eiar_list": {"type": "array", "items": {"type": "object"}},
            }, "required": ["eiar_list"]},
            backend="contact",
        ))


def register_sv_tools(node, bot_cfg):
    """给 skaye_sv 注册中心工具（record_lis 等）。"""
    from tll_protocol_v2 import sv_tools
    base_dir = str(BOTS / "skaye_sv")
    store = sv_tools.RegistryStore(base_dir)
    node._sv_store = store  # 供 ping 调度使用

    node.registry.register_backend("sv_record", sv_tools.create_record_lis_tool(store))
    node.registry.register_tool(ToolDefinition(
        name="record_lis", description="接收其他机器人上报的注册/心跳信息",
        parameters={"type": "object", "properties": {}}, backend="sv_record",
    ))
    node.registry.register_backend("sv_list", sv_tools.create_list_eiar_robots_tool(store))
    node.registry.register_tool(ToolDefinition(
        name="list_eiar_robots", description="获取所有 EiAr 组机器人的联系方式和工具",
        parameters={"type": "object", "properties": {}}, backend="sv_list",
    ))
    node.registry.register_backend("sv_grant", sv_tools.create_grant_permission_tool(store))
    node.registry.register_tool(ToolDefinition(
        name="grant_permission", description="授予权限",
        parameters={"type": "object", "properties": {
            "bot_id": {"type": "string"},
            "permission": {"type": "object"},
        }}, backend="sv_grant",
    ))
    node.registry.register_backend("sv_ping", sv_tools.create_ping_tool(store, node.config.bot_id))
    node.registry.register_tool(ToolDefinition(
        name="ping", description="心跳检测，返回 Skaye_SV 在线状态",
        parameters={"type": "object", "properties": {}}, backend="sv_ping",
    ))
    # TASK 集中汇聚：TASK 库 + 事件流（所有 bot 上报到这里，Skaye_SV 是中央信息源）
    archive_store = sv_tools.TaskArchiveStore(base_dir)
    node._sv_archive = archive_store
    node.registry.register_backend("sv_task_archive", sv_tools.create_task_archive_tool(archive_store))
    node.registry.register_tool(ToolDefinition(
        name="task_archive", description="接收其他机器人上报的 TASK 活动档（集中汇聚）",
        parameters={"type": "object", "properties": {"task_id": {"type": "string"}}}, backend="sv_task_archive",
    ))
    node.registry.register_backend("sv_event_report", sv_tools.create_event_report_tool(archive_store))
    node.registry.register_tool(ToolDefinition(
        name="event_report", description="接收其他机器人上报的运行事件",
        parameters={"type": "object", "properties": {}}, backend="sv_event_report",
    ))
    node.registry.register_backend("sv_query_task", sv_tools.create_query_task_tool(archive_store))
    node.registry.register_tool(ToolDefinition(
        name="query_task", description="查询 TASK 库：action=tasks(列表)/task(需task_id)/events(事件流)",
        parameters={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["tasks", "task", "events"]},
            "task_id": {"type": "string"},
            "limit": {"type": "integer"},
        }}, backend="sv_query_task",
    ))
    # 监控查询工具：查存档/机器人/统计（只读）
    from tll_protocol_v2 import dashboard_tools
    node.registry.register_backend("dashboard", dashboard_tools.create_dashboard_query_tool())
    node.registry.register_tool(ToolDefinition(
        name="dashboard_query",
        description="监控查询：查已注册机器人(action=robots)、最近任务(action=tasks)、单个任务(action=task,需task_id)、某bot运行档(action=bot_log,需bot)、统计(默认action=stats)",
        parameters={"type": "object", "properties": {
            "action": {"type": "string", "enum": ["robots", "tasks", "task", "bot_log", "stats"]},
            "task_id": {"type": "string"},
            "bot": {"type": "string"},
            "limit": {"type": "integer"},
        }},
        backend="dashboard",
    ))
    return store


async def sv_ping_loop(node, interval_s: float = 300.0):
    """Skaye_SV 自动 ping 调度：周期向注册的 bot 发 ping，更新 last_handshake。"""
    store = getattr(node, "_sv_store", None)
    if store is None:
        return
    while True:
        try:
            bots = store.all()
            for bot_id in bots:
                if bot_id == node.config.bot_id or "sv" in bot_id.lower():
                    continue
                task = Task(
                    task_type="general", from_bot=node.config.bot_id,
                    current_agent=node.config.bot_id,
                    tlljson=TLLjson(from_bot=node.config.bot_id, command="ping",
                                    to=bot_id, params={}),
                )
                node.mqtt.send_task(task, bot_id, f"tll/{bot_id}")
                print(f"[Skaye_SV] ping -> {bot_id}")
        except Exception as e:
            print(f"[Skaye_SV] ping 调度异常: {e}")
        await asyncio.sleep(interval_s)


async def main():
    bot_id = sys.argv[1] if len(sys.argv) > 1 else "eiar_001"
    bot_yaml = BOTS / bot_id / "bot.yaml"
    if not bot_yaml.is_file():
        print(f"未找到 {bot_yaml}"); return 1
    bot_cfg = yaml.safe_load(open(bot_yaml, encoding="utf-8")) or {}

    print("=" * 60)
    print(f"TLL v2 启动: {bot_cfg.get('id')} ({bot_cfg.get('name')})")
    print("=" * 60)

    # 从 bot.yaml 装配 node
    overrides = {}
    if bot_cfg.get("id") == SKAYE_SV:
        # skaye_sv 禁用 skills 扫描（用 V2 sv_tools 统一实现，避免旧 skill 冲突）
        overrides["skills_dir"] = ""
        overrides["tool_list_path"] = ""
    node = build_node_from_yaml(bot_yaml, **overrides)
    print(f"[mqtt] {node.config.mqtt_topic} @ {node.config.mqtt_host}:{node.config.mqtt_port}")

    is_sv = bot_cfg.get("id") == SKAYE_SV
    if is_sv:
        store = register_sv_tools(node, bot_cfg)
    else:
        register_ping(node, bot_cfg)
        # 牵线接口由 node 从 tool_list yaml（implements: contact）自动注册

    llm = build_llm(bot_cfg)
    if llm:
        print("[llm] DeepSeek 已初始化")

    loop = asyncio.get_running_loop()

    async def handle_incoming(task):
        print(f"\n[收到] {task.tlljson.command} from={task.from_bot}", flush=True)
        # skaye_sv 是中央：收到来自任何 bot 的消息即更新其握手时间
        if hasattr(node, "_sv_store") and task.from_bot and task.from_bot != node.config.bot_id:
            try:
                node._sv_store.update_handshake(task.from_bot)
            except Exception:
                pass
        # 无 LLM 节点（sayi_sv）：收到 from_bot==本机的任务 = 委托链回传
        # （web 以 sayi_sv 名义直接委托目标后，目标回传给发起者 sayi_sv）。
        # 目标 bot 处理完已自行上报 skaye_sv 存档，这里只需忽略，不处理不报错，
        # 避免无 LLM 走 Agent 报 NoneType，也不做自我委托。
        if not llm and task.from_bot == node.config.bot_id:
            print(f"[回传归档] {node.config.bot_id} 收到自本机的委托链回传 "
                  f"{task.tlljson.command}，忽略（目标已自行上报 skaye_sv）", flush=True)
            return
        if llm:
            final = await node.handle_new_task(task, llm)
        else:
            # 无 LLM：直接执行本地工具（如 skaye_sv 的 record_lis）
            final = await node.handle_new_task(task, None)
        task.output = final; task.result = final; task.status = TaskStatus.SUCCESS
        target = task.from_bot
        # 自我回环保护：回传目标是本机自己（from==本机）则不再回传，
        # 否则无 LLM bot 收到 chat 会无限自回环（chat→回传给自己→又收到→...）
        if target == node.config.bot_id:
            print(f"[回环] {node.config.bot_id} 收到自本机任务 {task.tlljson.command}，丢弃不回传", flush=True)
            return
        # 记录回传 hop（让委托链含完整往返轨迹：谁回传给谁）
        try:
            if getattr(task, "trace", None) is not None:
                task.trace.add_hop(node.config.bot_id, f"return_to_{target}")
        except Exception:
            pass
        print(f"[回传] -> {target}: {str(final)[:80]}", flush=True)
        node.mqtt.send_task(task, target, f"tll/{target}")
        # 补报最终结果给 skaye_sv（含 return hop + result），让大屏展示完整往返与对话结果
        try:
            if hasattr(node, "_report_task_to_sv_async"):
                node._report_task_to_sv_async(task)
        except Exception:
            pass

    def on_incoming(data, topic):
        try:
            task = Task.from_dict(data["task"])
        except Exception as e:
            return
        # 若是本机正在等回传（task_id 在 pending），由 tll.handle_response 消费，不走 LLM
        task_id = task.id
        consumed = node.tll.handle_response(task_id, data["task"])
        if consumed:
            print(f"[{node.config.bot_id}] 消费回传 task_id={task_id}", flush=True)
            return
        # 回传识别：task_id 是本机委托出去的任务（在 sent_tasks 里）——
        # 说明这是委托链回传，即使 pending 已超时清除（handle_response 未消费），
        # 也绝不该走 LLM/新任务处理（无 LLM 节点会报 NoneType，或误自我委托）。
        # 直接归档并忽略，避免把"直接任务调用的回传"误当成需要 LLM 的新任务。
        sent_ids = getattr(getattr(node, "tll", None), "sent_tasks", []) or []
        if any(getattr(t, "task_id", None) == task_id for t in sent_ids):
            print(f"[{node.config.bot_id}] 识别为委托链回传 task_id={task_id}（pending 已超时），忽略不走 LLM", flush=True)
            return
        print(f"[{node.config.bot_id}] 收到未消费 task_id={task_id} cmd={task.tlljson.command if task.tlljson else '?'} from={task.from_bot}", flush=True)
        loop.call_soon_threadsafe(lambda: loop.create_task(handle_incoming(task)))
    node.mqtt.on_envelope = on_incoming

    if not await asyncio.to_thread(node.mqtt.connect, 15):
        print("连接失败"); return 1
    print(f"[OK] 已连接 {node.config.mqtt_host}:{node.config.mqtt_port} 订阅 {node.config.mqtt_topic}")

    # 普通 bot：启动时上报
    if not is_sv:
        await report_to_sv(node, bot_cfg, SKAYE_SV)

    # Skaye_SV：启动自动 ping 调度
    if is_sv:
        loop.create_task(sv_ping_loop(node, interval_s=300.0))
        print("[Skaye_SV] 自动 ping 调度已启动 (每5分钟)")

    print("等待消息... (Ctrl+C 退出)\n", flush=True)
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    node.mqtt.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
