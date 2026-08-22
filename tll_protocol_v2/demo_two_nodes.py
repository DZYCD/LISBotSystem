"""双节点真实委托演示：sayi_996 委托 eiar_001 读文件。

- eiar_001 节点：注册本地 file_read 工具（本机读文件）
- sayi_996 节点：peers 含 eiar_001(file_read 白名单)，DeepSeek 调 task_create 委托
- 流程: sayi_996 收到"读文件"指令 → Agent 网络委托 eiar_001(file_read)
  → eiar_001 本地读文件 → 回传 → sayi_996 收到结果
"""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from tll_protocol_v2.core import Task, TaskStatus, TLLjson
from tll_protocol_v2.node import Node, NodeConfig

from lis_harness.adapters import DeepSeekClient
from lis_harness.registry import ToolDefinition

BASE = Path(__file__).resolve().parents[1]


def build_llm(bot_cfg):
    llm = bot_cfg["llm"]
    return DeepSeekClient(api_key=llm["api_key"], base_url=llm["base_url"], model=llm["model"])


def load(bot_id):
    p = BASE / "bots" / bot_id / "bot.yaml"
    return yaml.safe_load(open(p, encoding="utf-8"))


def register_file_read(node, workdir):
    """给节点注册本地 file_read 工具（本机读文件）。"""
    class FileReadBackend:
        name = "shell"
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            path = request.arguments.get("path", "")
            full = path if os.path.isabs(path) else os.path.join(workdir, path)
            try:
                with open(full, "r", encoding="utf-8") as f:
                    content = f.read()
                return ExecutionResult(ok=True, value={"path": path, "content": content})
            except Exception as e:
                return ExecutionResult(ok=False, error=f"read failed: {e}", denied=False)
    node.registry.register_backend("fileread", FileReadBackend())
    node.registry.register_tool(ToolDefinition(
        name="file_read", description="读取文件内容",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径"},
        }, "required": ["path"]},
        backend="fileread",
    ))


async def main():
    print("=" * 62)
    print("双节点真实委托: sayi_996 委托 eiar_001 读文件")
    print("=" * 62)

    # 读取两个 bot 配置
    sayi_cfg = load("sayi_996")
    eiar_cfg = load("eiar_001")
    sayi_net = sayi_cfg["networks"][0]
    eiar_net = eiar_cfg["networks"][0]

    # 测试文件
    test_file = Path(__file__).parent / "demo_readme.txt"
    test_file.write_text("这是由 eiar_001 本地读取的测试文件内容。", encoding="utf-8")

    # --- 节点 eiar_001 ---
    eiar = Node(NodeConfig(
        bot_id=eiar_cfg["id"], auth_key=eiar_cfg["auth_key"],
        peers={}, mqtt_host=eiar_net["url"], mqtt_port=eiar_net["port"],
        mqtt_topic=eiar_net["topic"],
        system_prompt=(
            "你是 LIS 集群的编程助手 eiar_001。"
            "你有且只有一个本地工具 file_read（参数 path: 要读取的文件路径）。"
            "当你收到任何读取文件内容的请求时，你必须调用 file_read 工具，"
            "把读到的内容原样返回。"
            "请求里通常会给出文件路径（可能在 '请读取文件 X 的内容' 或 params 中）。"
            "绝对不要只说空话，必须调用 file_read 读取后返回内容。"
        ),
    ))
    register_file_read(eiar, str(Path(__file__).parent))
    eiar_llm = build_llm(eiar_cfg)
    print(f"\n[节点] {eiar_cfg['id']} (提供 file_read 本地工具)", flush=True)

    # --- 节点 sayi_996（用脚本化 LLM，固定委托 eiar_001，隔离验证委托链） ---
    sayi = Node(NodeConfig(
        bot_id=sayi_cfg["id"], auth_key=sayi_cfg["auth_key"],
        peers=sayi_cfg["peers"],  # 含 eiar_001(file_read 白名单)
        mqtt_host=sayi_net["url"], mqtt_port=sayi_net["port"],
        mqtt_topic=sayi_net["topic"],
    ))
    # sayi 的 mock LLM：固定调 task_create 委托 eiar_001 读文件
    from lis_harness.llm import MockLlmClient, LlmResult, call_tool, text as _text
    _s = {"n": 0}
    def sayi_script(messages, tools):
        n = _s["n"]; _s["n"] += 1
        if n == 0:
            return LlmResult(blocks=[call_tool("task_create", {
                "to": "agent/eiar_001", "command": "file_read",
                "params": {"path": str(test_file)},
            })])
        # 第2轮：从 tool-result 提取 eiar 回传（解析 content）
        from lis_harness.session import ToolResultBlock
        result = ""
        for m in messages:
            for b in (m.content if isinstance(m.content, list) else []):
                if isinstance(b, ToolResultBlock):
                    result = b.content
        # 尝试解析出 content 字段
        try:
            import json as _j
            d = _j.loads(result)
            if isinstance(d, dict) and "content" in d:
                result = f"文件内容: {d['content']}"
        except Exception:
            pass
        return LlmResult(blocks=[_text(f"已委托 eiar_001 读取文件，结果: {result}")])
    sayi_llm = MockLlmClient(sayi_script)
    print(f"[节点] {sayi_cfg['id']} (脚本化 LLM，固定委托 eiar_001.file_read)", flush=True)
    print(f"[节点] {sayi_cfg['id']} (可委托 eiar_001.file_read)", flush=True)

    # 连接
    if not await asyncio.to_thread(eiar.mqtt.connect, 15):
        print("eiar connect fail"); return 1
    if not await asyncio.to_thread(sayi.mqtt.connect, 15):
        print("sayi connect fail"); return 1
    print("[MQTT] 两节点已连接 broker.emqx.io", flush=True)

    loop = asyncio.get_running_loop()

    # eiar_001 收消息 → Agent → 回传
    async def eiar_handle(task):
        print(f"[eiar] 收到委托 cmd={task.tlljson.command} from={task.from_bot}", flush=True)
        final = await eiar.handle_new_task(task, eiar_llm)
        task.output = final; task.result = final; task.status = TaskStatus.SUCCESS
        target = task.from_bot
        print(f"[eiar] 回传 -> {target}: {final[:80]}", flush=True)
        eiar.mqtt.send_task(task, target, f"tll/{target}")
    def on_eiar(data, topic):
        loop.call_soon_threadsafe(lambda: loop.create_task(eiar_handle(Task.from_dict(data["task"]))))
    eiar.mqtt.on_envelope = on_eiar

    # sayi_996 收到用户指令 → Agent → 委托 eiar_001 → 回传用户
    async def sayi_handle(task):
        print(f"[sayi] 收到用户指令: {task.tlljson.params.get('text','')[:50]}", flush=True)
        final = await sayi.handle_new_task(task, sayi_llm)
        task.output = final; task.result = final; task.status = TaskStatus.SUCCESS
        target = task.from_bot
        print(f"[sayi] 回传 -> {target}:\n{final}", flush=True)
        sayi.mqtt.send_task(task, target, f"tll/{target}")
    def on_sayi(data, topic):
        loop.call_soon_threadsafe(lambda: loop.create_task(sayi_handle(Task.from_dict(data["task"]))))
    sayi.mqtt.on_envelope = on_sayi

    # 模拟用户给 sayi_996 发指令
    print("\n[用户] 给 sayi_996 发指令: 读 demo_readme.txt", flush=True)
    user_task = Task(
        task_type="general", from_bot="agent/test_user", current_agent="agent/sayi_996",
        tlljson=TLLjson(from_bot="agent/test_user", command="chat", to="agent/sayi_996",
                        params={"text": f"请用 file_read 读取 {test_file} 的内容" }),
    )
    sayi.mqtt.send_task(user_task, "agent/sayi_996", f"tll/agent/sayi_996")

    # 等待整条链完成（DeepSeek + 网络往返需时间）
    await asyncio.sleep(30)
    print("\n[验证窗口结束]", flush=True)
    eiar.mqtt.close(); sayi.mqtt.close()
    return 0

if __name__ == "__main__":
    asyncio.run(main())
