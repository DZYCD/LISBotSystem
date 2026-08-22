"""审批服务与执行管线的集成测试。

覆盖机制：pre-execute(allow/ask/deny) → 审批 → 每调用解析策略 → 能力后端执行。
"""

import asyncio
import unittest
from pathlib import Path
from typing import List

from lis_harness.security import (
    ApprovalOutcome,
    CallbackApprovalService,
    ExecutionPipeline,
    ExecutionRequest,
    PreExecuteVerdict,
    PreToolDecision,
    SandboxMode,
    SandboxPolicyResolver,
)
from lis_harness.security.backends import InProcessShell


def run(coro):
    return asyncio.run(coro)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.workspace = Path("/workspace")
        self.resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.WORKSPACE_WRITE,
            workspace_root=self.workspace,
        )
        self.approval_requests: List[tuple] = []

        def prompt(request, reason):
            self.approval_requests.append((request, reason))
            return ApprovalOutcome.ALLOWED_ONCE

        self.approval = CallbackApprovalService(prompt)
        self.pipeline = ExecutionPipeline(
            policy_resolver=self.resolver,
            approval=self.approval,
        )
        self.shell = InProcessShell()

    def _req(self, op, path, actor="u1", requested_mode=None):
        return ExecutionRequest(
            tool_name="shell",
            arguments={"op": op, "path": str(path)},
            actor=actor,
            requested_mode=requested_mode,
        )

    def test_default_allow_within_workspace(self):
        result = run(self.pipeline.execute(
            self._req("write", self.workspace / "a.txt"),
            self.shell,
        ))
        self.assertTrue(result.ok)
        self.assertFalse(result.denied)
        # 默认档 WORKSPACE_WRITE，写工作区内放行
        self.assertEqual(result.value["mode"], "workspace-write")

    def test_default_deny_outside_workspace_without_approval(self):
        # 没有审批监听，默认 ALLOW，但后端因超范围返回 denied
        result = run(self.pipeline.execute(
            self._req("write", Path("/etc/passwd")),
            self.shell,
        ))
        self.assertFalse(result.ok)
        self.assertTrue(result.denied)
        self.assertIn("denied", result.error)

    def test_ask_then_approve_escalates_for_this_call(self):
        # 注册一个 ask 监听器：请求写工作区外时，要求审批并升级到 full access
        def ask_listener(request):
            return PreExecuteVerdict(
                PreToolDecision.ASK,
                reason="writing outside workspace",
                requested_mode=SandboxMode.DANGER_FULL_ACCESS,
            )

        self.pipeline.add_pre_execute_listener(ask_listener)

        result = run(self.pipeline.execute(
            self._req("write", Path("/etc/passwd")),
            self.shell,
        ))
        self.assertTrue(result.ok)
        self.assertFalse(result.denied)
        self.assertEqual(result.value["mode"], "danger-full-access")
        # 审批确实被请求了一次
        self.assertEqual(len(self.approval_requests), 1)

    def test_ask_then_reject_blocks(self):
        def prompt(request, reason):
            return ApprovalOutcome.REJECTED

        self.approval = CallbackApprovalService(prompt)
        self.pipeline = ExecutionPipeline(
            policy_resolver=self.resolver,
            approval=self.approval,
        )

        def ask_listener(request):
            return PreExecuteVerdict(PreToolDecision.ASK, reason="needs approval")

        self.pipeline.add_pre_execute_listener(ask_listener)

        result = run(self.pipeline.execute(
            self._req("write", Path("/etc/passwd")),
            self.shell,
        ))
        self.assertFalse(result.ok)
        self.assertTrue(result.denied)
        self.assertIn("rejected", result.error)

    def test_deny_listener_blocks_without_approval(self):
        def deny_listener(request):
            return PreExecuteVerdict(PreToolDecision.DENY, reason="tool disabled")

        self.pipeline.add_pre_execute_listener(deny_listener)

        result = run(self.pipeline.execute(
            self._req("write", self.workspace / "a.txt"),
            self.shell,
        ))
        self.assertFalse(result.ok)
        self.assertTrue(result.denied)
        # deny 监听器提供的 reason 透传给结果
        self.assertIn("tool disabled", result.error)
        # deny 不会触发审批
        self.assertEqual(len(self.approval_requests), 0)

    def test_read_allowed_even_in_read_only(self):
        self.resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.READ_ONLY,
            workspace_root=self.workspace,
        )
        self.pipeline = ExecutionPipeline(
            policy_resolver=self.resolver,
            approval=self.approval,
        )
        result = run(self.pipeline.execute(
            self._req("read", self.workspace / "a.txt"),
            self.shell,
        ))
        self.assertTrue(result.ok)
        self.assertEqual(result.value["mode"], "read-only")


if __name__ == "__main__":
    unittest.main()
