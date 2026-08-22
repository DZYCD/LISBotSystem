"""失败闭环测试：工具调用失败必须转成 tool/result 喂给模型，不崩溃。"""

import asyncio
import unittest

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
from lis_harness.session import Session, ToolCallBlock, ToolResultBlock


def run(coro):
    return asyncio.run(coro)


class _FailingBackend:
    """一个总是抛异常的后端，验证异常被闭环处理。"""

    name = "shell"

    async def execute(self, request, policy):
        raise RuntimeError("backend exploded")


class FailureClosureTest(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        approval = CallbackApprovalService(lambda r, reason: ApprovalOutcome.ALLOWED_ONCE)
        resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.DANGER_FULL_ACCESS,
            workspace_root=None,
        )
        pipeline = ExecutionPipeline(policy_resolver=resolver, approval=approval)
        self.tool_runtime = ToolRuntime(self.registry, pipeline)

    def _agent(self, script, runtime=None):
        llm = MockLlmClient(script)
        return Agent(llm, runtime or self.tool_runtime)

    def _result_texts(self, agent):
        """提取日志里所有 tool/result 的 content 文本。"""
        texts = []
        for e in agent.session.events:
            if e.type == "tool/result":
                block = e.data["content"]
                texts.append(block.content)
        return texts

    def test_unknown_tool_returns_error_result_not_crash(self):
        # 模型调用不存在的工具，不应崩溃，应返回带错误信息的 tool/result
        state = {"called": False}

        def script(messages, tools):
            if not state["called"]:
                state["called"] = True
                return LlmResult(blocks=[call_tool("nonexistent", {})])
            # 第二步：模型应该能看到失败的 tool/result
            return LlmResult(blocks=[text("recovered")])

        agent = self._agent(script)
        result = run(agent.run("do it"))
        self.assertEqual(result.reason, "completed")
        # 没有崩溃，模型继续
        self.assertEqual(result.final_text, "recovered")
        # tool/result 里应有错误信息
        texts = self._result_texts(agent)
        self.assertTrue(any("nonexistent" in t and "error" in t.lower() for t in texts))

    def test_invalid_json_arguments_returns_error(self):
        state = {"called": False}

        def script(messages, tools):
            if not state["called"]:
                state["called"] = True
                return LlmResult(blocks=[ToolCallBlock(
                    id="c1", name="bash", arguments="{this is not json",
                )])
            return LlmResult(blocks=[text("saw error")])

        agent = self._agent(script)
        result = run(agent.run("x"))
        self.assertEqual(result.reason, "completed")
        self.assertEqual(result.final_text, "saw error")
        texts = self._result_texts(agent)
        self.assertTrue(any("invalid JSON" in t for t in texts))

    def test_backend_exception_returns_error_not_crash(self):
        state = {"called": False}

        def script(messages, tools):
            if not state["called"]:
                state["called"] = True
                return LlmResult(blocks=[call_tool("bash", {"command": "x"})])
            return LlmResult(blocks=[text("after failure")])

        # 注册一个会抛异常的后端
        failing = _FailingBackend()
        self.registry.register_backend("shell", failing)
        self.registry.register_tool(ToolDefinition(
            name="bash", description="x", parameters={}, backend="shell",
        ))

        agent = self._agent(script)
        result = run(agent.run("y"))
        self.assertEqual(result.reason, "completed")
        self.assertEqual(result.final_text, "after failure")
        texts = self._result_texts(agent)
        self.assertTrue(any("backend exploded" in t for t in texts))

    def test_every_tool_call_has_matching_result(self):
        # 无论成败，每个 tool/call 都应有对应 tool/result（日志完整闭环）
        state = {"n": 0}

        def script(messages, tools):
            n = state["n"]
            state["n"] += 1
            if n < 3:
                # 交替调用存在的/不存在的工具
                name = "bash" if n % 2 == 0 else "ghost_tool"
                return LlmResult(blocks=[call_tool(name, {"command": "echo hi"})])
            return LlmResult(blocks=[text("done")])

        # 只注册 bash，ghost_tool 不存在
        self.registry.register_backend("shell", RuntimeErrorBackend())
        self.registry.register_tool(ToolDefinition(
            name="bash", description="x", parameters={}, backend="shell",
        ))

        agent = self._agent(script)
        run(agent.run("z"))
        calls = [e for e in agent.session.events if e.type == "tool/call"]
        results = [e for e in agent.session.events if e.type == "tool/result"]
        # 每个 tool/call 都配对到 tool/result
        self.assertEqual(len(calls), len(results))
        # 配对校验：call_id 一致
        call_ids = {e.data["call_id"] for e in calls}
        result_ids = {e.data["content"].tool_call_id for e in results}
        self.assertEqual(call_ids, result_ids)


class RuntimeErrorBackend:
    """让 bash 工具执行成功但返回结果的假后端。"""

    name = "shell"

    async def execute(self, request, policy):
        from lis_harness.security.capability import ExecutionResult
        return ExecutionResult(ok=True, value={"exit_code": 0})


class AgentOptionsIsolationTest(unittest.TestCase):
    def test_default_options_are_not_shared(self):
        from lis_harness.agent import AgentOptions
        a1 = Agent(MockLlmClient(lambda m, t: LlmResult(blocks=[text("x")])), None)
        a2 = Agent(MockLlmClient(lambda m, t: LlmResult(blocks=[text("x")])), None)
        self.assertIsNot(a1.options, a2.options)
        # 修改一个不影响另一个
        a1.options.max_steps = 99
        self.assertEqual(a2.options.max_steps, 10)


if __name__ == "__main__":
    unittest.main()
