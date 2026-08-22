"""用 eiar_001 的真实配置启动 TLL v2 节点（真实 MQTT + 真实 DeepSeek LLM）。

从 eiar_001/bot.yaml 读取 MQTT 配置、auth_key、peers、LLM 配置。
监听 tll/agent/eiar_001，用 harness Agent 循环处理收到的委托任务。
"""

import asyncio
import sys
import os
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from tll_protocol_v2.core import Task, TaskStatus, TLLjson
from tll_protocol_v2.node import Node, NodeConfig

from lis_harness.adapters import DeepSeekClient
from lis_harness.llm import LlmResult, TextBlock
from lis_harness.registry import ToolDefinition

BOT_YAML = Path(__file__).resolve().parents[1] / "bots" / "eiar_001" / "bot.yaml"


def load_bot_config() -> dict:
    with open(BOT_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_llm(bot_cfg: dict, node: Node):
    """从 bot.yaml 的 llm 段构造真实 DeepSeek LLM。"""
    llm = bot_cfg.get("llm", {})
    if not llm.get("enabled"):
        raise RuntimeError("eiar_001 llm.enabled is false")
    client = DeepSeekClient(
        api_key=llm.get("api_key"),
        base_url=llm.get("base_url"),
        model=llm.get("model") or "deepseek-chat",
    )
    # 预置 task_create 工具 schema（让 LLM 知道能委托谁）
    return client


def register_ping_tool(node: Node):
    """注册一个 ping 本地工具（eiar_001 无 skills 实现，提供最小可测工具）。"""
    class PingBackend:
        name = "shell"
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            return ExecutionResult(ok=True, value={"pong": True, "bot": node.config.bot_id})
    node.registry.register_backend("ping", PingBackend())
    node.registry.register_tool(ToolDefinition(
        name="ping", description="心跳检测，返回 PONG",
        parameters={"type": "object", "properties": {}}, backend="ping",
    ))


async def main():
    print("=" * 60)
    print("TLL v2 真实启动: eiar_001 配置")
    print("=" * 60)
    bot_cfg = load_bot_config()
    print(f"[bot] {bot_cfg['id']} name={bot_cfg['name']}")

    # 取第一个 mqtt network
    net = bot_cfg["networks"][0]
    print(f"[mqtt] {net['url']}:{net['port']} topic={net['topic']}")

    # peers：委托白名单
    peers = bot_cfg.get("peers", {})

    node = Node(NodeConfig(
        bot_id=bot_cfg["id"],
        auth_key=bot_cfg["auth_key"],
        peers=peers,
        mqtt_host=net["url"],
        mqtt_port=net["port"],
        mqtt_topic=net["topic"],
    ))
    register_ping_tool(node)

    # 真实 DeepSeek LLM
    llm = build_llm(bot_cfg, node)
    print("[llm] DeepSeek 已初始化")

    loop = asyncio.get_running_loop()

    # 收到新任务 → 跑 Agent → 回传
    async def handle_incoming(task):
        print(f"\n[收到] 任务 {task.id} cmd={task.tlljson.command} from={task.from_bot}", flush=True)
        final = await node.handle_new_task(task, llm)
        task.output = final; task.result = final
        task.status = TaskStatus.SUCCESS
        target = task.from_bot
        print(f"[回传] -> {target}: {final[:80]}", flush=True)
        node.mqtt.send_task(task, target, f"tll/{target}")

    def on_incoming(data, topic):
        try:
            task = Task.from_dict(data["task"])
        except Exception as e:
            print(f"[解析失败] {e}", flush=True); return
        loop.call_soon_threadsafe(lambda: loop.create_task(handle_incoming(task)))
    node.mqtt.on_envelope = on_incoming

    print("连接真实 broker...", flush=True)
    if not await asyncio.to_thread(node.mqtt.connect, 15):
        print("连接失败"); return 1
    print(f"[OK] 已连接 {net['url']}:{net['port']} 订阅 {net['topic']}")
    print("等待消息... (Ctrl+C 退出)\n", flush=True)

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    node.mqtt.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
