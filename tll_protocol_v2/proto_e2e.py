"""端到端验证 v2：两节点真实 MQTT 委托（正确 async 并发模型）。

关键：A 的处理（含 task_create 委托等待回传）和 B 的接收必须都在主事件循环，
且 A await 回传时循环能切换到 B 的处理。用 asyncio.create_task 并发驱动。
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tll_protocol_v2.core import Task, TaskStatus, TLLjson
from tll_protocol_v2.node import Node, NodeConfig

from lis_harness.llm import LlmResult, MockLlmClient, call_tool, text
from lis_harness.registry import ToolDefinition


class LocalBackend:
    name = "shell"
    async def execute(self, request, policy):
        from lis_harness.security.capability import ExecutionResult
        op = request.arguments.get("op"); path = request.arguments.get("path")
        if op == "write":
            with open(path, "w", encoding="utf-8") as f:
                f.write(request.arguments.get("content", ""))
            return ExecutionResult(ok=True, value={"effect": "write", "path": path})
        return ExecutionResult(ok=False, error="unknown op", denied=False)


def llm_a():
    s = {"n": 0}
    def script(messages, tools):
        n = s["n"]; s["n"] += 1
        if n == 0:
            return LlmResult(blocks=[call_tool("task_create", {
                "to": "agent/v2_b", "command": "do_work", "params": {"req": "ping"},
            })])
        return LlmResult(blocks=[text("A收到回传完成")])
    return MockLlmClient(script)


def llm_b():
    s = {"n": 0}
    def script(messages, tools):
        n = s["n"]; s["n"] += 1
        if n == 0:
            return LlmResult(blocks=[call_tool("local_op", {"op": "write", "path": "b_out.txt", "content": "B done"})])
        return LlmResult(blocks=[text("B完成")])
    return MockLlmClient(script)


async def main():
    print("1. 构建节点...", flush=True)
    node_a = Node(NodeConfig(bot_id="agent/v2_a", auth_key="sk-a", peers={"agent/v2_b": {"auth_key": "sk-b", "tools": [{"name": "do_work"}]}}, mqtt_host="broker.emqx.io", mqtt_port=1883, mqtt_topic="tll/agent/v2_a"))
    node_b = Node(NodeConfig(bot_id="agent/v2_b", auth_key="sk-b", peers={"agent/v2_a": {"auth_key": "sk-a", "tools": []}}, mqtt_host="broker.emqx.io", mqtt_port=1883, mqtt_topic="tll/agent/v2_b"))
    for n in (node_a, node_b):
        n.registry.register_backend("shell", LocalBackend())
        n.registry.register_tool(ToolDefinition(name="local_op", description="local", parameters={}, backend="shell"))
    print("2. 连接 A...", flush=True)
    if not await asyncio.to_thread(node_a.mqtt.connect, 10): print("A connect fail"); return 1
    print("3. 连接 B...", flush=True)
    if not await asyncio.to_thread(node_b.mqtt.connect, 10): print("B connect fail"); return 1
    print("4. 连接完成", flush=True)

    la, lb = llm_a(), llm_b()
    loop = asyncio.get_running_loop()

    # B 的接收：新任务 → 跑 B 的 Agent → 回传
    async def handle_b_task(task):
        print(f"[B] 收到新任务 {task.tlljson.command}", flush=True)
        final = await node_b.handle_new_task(task, lb)
        task.output = final; task.result = final
        task.status = TaskStatus.SUCCESS
        target = task.from_bot
        print(f"[B] -> {target}: {final}", flush=True)
        node_b.mqtt.send_task(task, target, f"tll/{target}")

    def on_b_envelope(data, topic):
        try:
            task = Task.from_dict(data["task"])
        except Exception as e:
            print(f"[B] 解析失败: {e}", flush=True); return
        loop.call_soon_threadsafe(lambda: loop.create_task(handle_b_task(task)))
    node_b.mqtt.on_envelope = on_b_envelope

    task_for_a = Task(
        from_bot="agent/user", current_agent="agent/v2_a",
        tlljson=TLLjson(from_bot="agent/user", command="chat", to="agent/v2_a",
                        params={"text": "让 B 干活"}),
    )
    print("5. A 开始处理（委托 B）...", flush=True)
    result = await node_a.handle_new_task(task_for_a, la)
    print(f"A 最终: {result}", flush=True)

    await asyncio.sleep(1)
    ok = "完成" in result and os.path.exists("b_out.txt")
    if os.path.exists("b_out.txt"):
        print(f"B 本地工具: {open('b_out.txt', encoding='utf-8').read()!r}", flush=True)
    print(f"\n[结论] {'成功 [OK]' if ok else '失败 [FAIL]'}", flush=True)
    node_a.mqtt.close(); node_b.mqtt.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
