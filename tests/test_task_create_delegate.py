"""整合核心测试：Agent 循环里 task_create 同步阻塞委托 + TASK id 复用 + 回传。"""

import asyncio
import unittest

from lis_harness.adapters import TLLTransport, TLLTransportConfig
from lis_harness.agent import Agent
from lis_harness.llm import LlmResult, MockLlmClient, call_tool, text
from lis_harness.registry import Registry, ToolDefinition, ToolRuntime
from lis_harness.security import (
    ApprovalOutcome,
    CallbackApprovalService,
    ExecutionPipeline,
    SandboxMode,
    SandboxPolicyResolver,
)
from lis_harness.session import ToolCallBlock, ToolResultBlock
from lis_harness.tools.task_create_tool import create as make_task_create


def run(coro):
    return asyncio.run(coro)


class TaskCreateDelegateTest(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        approval = CallbackApprovalService(lambda r, reason: ApprovalOutcome.ALLOWED_ONCE)
        resolver = SandboxPolicyResolver(default_mode=SandboxMode.DANGER_FULL_ACCESS, workspace_root=None)
        pipeline = ExecutionPipeline(policy_resolver=resolver, approval=approval)
        self.tool_runtime = ToolRuntime(self.registry, pipeline)

        self.tll = TLLTransport(TLLTransportConfig(
            my_bot_id="agent/eiar_001",
            peers={"agent/sayi_996": {"tools": [{"name": "write_code"}, {"name": "ping"}]}},
            timeout_s=5,
        ))
        self.registry.register_backend("tll", self.tll)
        self.registry.register_tool(make_task_create({}))
        self.tll.register_peer_handler("agent/sayi_996", "write_code", lambda p: {"code": "done"})

    def _agent(self, script):
        llm = MockLlmClient(script)
        return Agent(llm, self.tool_runtime)

    def test_task_create_delegates_and_returns_result_to_llm(self):
        # LLM 第1轮调 task_create 委托，第2轮应该能看到回传结果（tool-result）
        state = {"n": 0}

        def script(messages, tools):
            n = state["n"]
            state["n"] += 1
            if n == 0:
                return LlmResult(blocks=[call_tool("task_create", {
                    "to": "agent/sayi_996", "command": "write_code", "params": {"req": "write app"},
                })])
            return LlmResult(blocks=[text("got code result")])

        agent = self._agent(script)
        result = run(agent.run("write code"))
        self.assertEqual(result.reason, "completed")
        self.assertEqual(result.final_text, "got code result")

        # 校验：发出的委托任务
        self.assertEqual(len(self.tll.sent_tasks), 1)
        task = self.tll.sent_tasks[0]
        self.assertEqual(task.to, "agent/sayi_996")
        self.assertEqual(task.command, "write_code")

        # 校验：回传结果作为 tool-result 进入 LLM 第2轮
        # 第2轮 messages 里应有 tool-result 内容包含委托结果
        self.assertTrue(state["n"] >= 2)

    def test_task_id_reuse_when_delegating_from_in_progress_task(self):
        # LLM 当前处理一个 task（reuse_task_id），再委托应复用该 id
        state = {"n": 0}

        def script(messages, tools):
            n = state["n"]
            state["n"] += 1
            if n == 0:
                return LlmResult(blocks=[call_tool("task_create", {
                    "to": "agent/sayi_996", "command": "write_code",
                    "params": {}, "task_id": "chain-123",
                })])
            return LlmResult(blocks=[text("done")])

        agent = self._agent(script)
        run(agent.run("x"))
        self.assertEqual(self.tll.sent_tasks[0].task_id, "chain-123")

    def test_multiple_tool_calls_serial_first_only(self):
        # 模型一次输出多个 tool_call，应串行全部执行（每个都有结果），不丢弃
        state = {"n": 0}

        def script(messages, tools):
            n = state["n"]
            state["n"] += 1
            if n == 0:
                return LlmResult(blocks=[
                    call_tool("task_create", {"to": "agent/sayi_996", "command": "write_code", "params": {}}),
                    call_tool("task_create", {"to": "agent/sayi_996", "command": "ping", "params": {}}),
                ])
            return LlmResult(blocks=[text("serial done")])

        agent = self._agent(script)
        run(agent.run("y"))
        # 串行执行全部：两个 task_create 都发出
        self.assertEqual(len(self.tll.sent_tasks), 2)
        self.assertEqual(self.tll.sent_tasks[0].command, "write_code")
        self.assertEqual(self.tll.sent_tasks[1].command, "ping")
        # 每个 tool_call 都有对应 tool/result（DeepSeek 配对要求）
        trs = [e for e in agent.session.events if e.type == "tool/result"]
        self.assertEqual(len(trs), 2)


if __name__ == "__main__":
    unittest.main()
