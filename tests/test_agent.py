"""Agent 循环测试：多步推理、工具调用回填、日志持久化。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from lis_harness.agent import Agent, AgentOptions
from lis_harness.llm import LlmResult, MockLlmClient, call_tool, text
from lis_harness.registry import Registry, ToolCall, ToolDefinition, ToolRuntime
from lis_harness.security import (
    ApprovalOutcome,
    CallbackApprovalService,
    ExecutionPipeline,
    SandboxMode,
    SandboxPolicyResolver,
)
from lis_harness.security.backends import InProcessShell
from lis_harness.session import Message, Session, ToolCallBlock, ToolResultBlock


def run(coro):
    return asyncio.run(coro)


class FakeShellBackend(InProcessShell):
    """进程内假后端：记录被调用的参数，供脚本验证。"""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def execute(self, request, policy):
        self.calls.append(request.arguments)
        return await super().execute(request, policy)


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.backend = FakeShellBackend()
        self.registry.register_backend("shell", self.backend)
        self.registry.register_tool(ToolDefinition(
            name="bash", description="run shell", parameters={}, backend="shell",
        ))
        approval = CallbackApprovalService(lambda r, reason: ApprovalOutcome.ALLOWED_ONCE)
        resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.DANGER_FULL_ACCESS,
            workspace_root=None,
        )
        pipeline = ExecutionPipeline(policy_resolver=resolver, approval=approval)
        self.tool_runtime = ToolRuntime(self.registry, pipeline)

    def _make_agent(self, script):
        llm = MockLlmClient(script)
        return Agent(llm, self.tool_runtime)

    def test_single_step_no_tool(self):
        def script(messages, tools):
            return LlmResult(blocks=[text("direct answer")])

        agent = self._make_agent(script)
        result = run(agent.run("hello"))
        self.assertEqual(result.final_text, "direct answer")
        self.assertEqual(result.reason, "completed")
        # 日志应有完整闭环
        types = [e.type for e in agent.session.events]
        self.assertIn("turn/start", types)
        self.assertIn("user/message", types)
        self.assertIn("assistant/message", types)
        self.assertIn("turn/end", types)

    def test_multi_step_tool_then_answer(self):
        # 第一步：调 bash 工具；第二步：看到工具结果后给最终答案
        state = {"called": False}

        def script(messages, tools):
            if not state["called"]:
                state["called"] = True
                return LlmResult(blocks=[call_tool("bash", {"command": "echo hi"})])
            # 第二步：验证模型历史里已有 tool-result
            has_result = any(
                b.type == "tool-result"
                for m in messages
                for b in m.content
            )
            self.assertTrue(has_result, "model history must contain tool result")
            return LlmResult(blocks=[text("got it")])

        agent = self._make_agent(script)
        result = run(agent.run("do something"))
        self.assertEqual(result.final_text, "got it")
        self.assertEqual(result.reason, "completed")
        self.assertGreater(result.steps, 1)
        # 后端确实被调用了
        self.assertEqual(len(self.backend.calls), 1)

    def test_tool_result_written_to_log(self):
        def script(messages, tools):
            return LlmResult(blocks=[call_tool("bash", {"command": "echo hi"})])

        agent = self._make_agent(script)
        run(agent.run("x"))
        types = [e.type for e in agent.session.events]
        self.assertIn("tool/call", types)
        self.assertIn("tool/result", types)
        # 找到 tool/result，验证它携带了对应 call_id
        tr = [e for e in agent.session.events if e.type == "tool/result"][0]
        self.assertIsInstance(tr.data["content"], ToolResultBlock)

    def test_session_persistence_and_replay(self):
        state = {"called": False}

        def script(messages, tools):
            if not state["called"]:
                state["called"] = True
                return LlmResult(blocks=[call_tool("bash", {"command": "echo hi"})])
            return LlmResult(blocks=[text("done")])

        agent = self._make_agent(script)
        run(agent.run("do it"))
        dumped = agent.session.dump()
        restored = Session.restore(dumped)
        self.assertEqual(restored.session_id, agent.session.session_id)
        # 回放后历史一致
        self.assertEqual(len(restored.derive_messages()), len(agent.session.derive_messages()))
        # 通过回放恢复的 agent 能继续
        agent2 = Agent(
            MockLlmClient(lambda m, t: LlmResult(blocks=[text("continued")])),
            self.tool_runtime,
            session=restored,
        )
        result = run(agent2.run("continue"))
        self.assertEqual(result.final_text, "continued")

    def test_max_steps_prevents_infinite_loop(self):
        def script(messages, tools):
            return LlmResult(blocks=[call_tool("bash", {"command": "echo hi"})])

        agent = self._make_agent(script)
        agent.options.max_steps = 3
        result = run(agent.run("loop"))
        self.assertEqual(result.reason, "max-steps")


class SystemLayersTest(unittest.TestCase):
    """验证分层系统提示词：稳定层在前、注入为多条 system 消息。"""

    def test_system_layers_injected_in_order(self):
        captured = {}
        def script(messages, tools):
            captured["messages"] = messages
            return LlmResult(blocks=[text("ok")])

        llm = MockLlmClient(script)
        options = AgentOptions(system_layers=[
            "基础层：角色设定与说话规则（稳定）",
            "工具层：可用工具与委托白名单（次稳定）",
        ])
        agent = Agent(llm, None, options=options)
        run(agent.run("hi"))

        msgs = captured["messages"]
        roles = [m.role for m in msgs]
        self.assertEqual(roles[:2], ["system", "system"])
        self.assertEqual(roles[2], "user")
        sys_texts = [b.text for m in msgs[:2] for b in m.content if hasattr(b, "text")]
        self.assertIn("基础层", sys_texts[0])
        self.assertIn("工具层", sys_texts[1])

    def test_system_layers_only_injected_once(self):
        counts = []
        def script(messages, tools):
            counts.append(sum(1 for m in messages if m.role == "system"))
            return LlmResult(blocks=[text("ok")])

        llm = MockLlmClient(script)
        options = AgentOptions(system_layers=["层1", "层2"])
        agent = Agent(llm, None, options=options)
        run(agent.run("a"))
        run(agent.run("b"))
        # 每次请求 system 数量保持 2（不重复注入）
        self.assertEqual(counts, [2, 2])


if __name__ == "__main__":
    unittest.main()
