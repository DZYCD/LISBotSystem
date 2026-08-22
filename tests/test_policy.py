"""沙箱模式与策略解析的单元测试。"""

import unittest
from pathlib import Path

from lis_harness.security import (
    ExecutionRequest,
    SandboxMode,
    SandboxPolicy,
    SandboxPolicyResolver,
)


class SandboxModeTest(unittest.TestCase):
    def test_allows_write_by_mode(self):
        self.assertFalse(SandboxMode.READ_ONLY.allows_write)
        self.assertTrue(SandboxMode.WORKSPACE_WRITE.allows_write)
        self.assertTrue(SandboxMode.DANGER_FULL_ACCESS.allows_write)

    def test_upgrade_ranks(self):
        self.assertTrue(SandboxMode.READ_ONLY.can_upgrade_to(SandboxMode.WORKSPACE_WRITE))
        self.assertTrue(SandboxMode.WORKSPACE_WRITE.can_upgrade_to(SandboxMode.DANGER_FULL_ACCESS))
        self.assertFalse(SandboxMode.WORKSPACE_WRITE.can_upgrade_to(SandboxMode.READ_ONLY))
        self.assertFalse(SandboxMode.WORKSPACE_WRITE.can_upgrade_to(SandboxMode.WORKSPACE_WRITE))


class SandboxPolicyTest(unittest.TestCase):
    def setUp(self):
        self.workspace = Path("/workspace")

    def test_read_only_rejects_all_writes(self):
        policy = SandboxPolicy(mode=SandboxMode.READ_ONLY, workspace_root=self.workspace)
        self.assertFalse(policy.allows_write_to(self.workspace / "a.txt"))

    def test_full_access_allows_any_path(self):
        policy = SandboxPolicy(mode=SandboxMode.DANGER_FULL_ACCESS, workspace_root=self.workspace)
        self.assertTrue(policy.allows_write_to(Path("/etc/passwd")))

    def test_workspace_write_only_inside_root(self):
        policy = SandboxPolicy(mode=SandboxMode.WORKSPACE_WRITE, workspace_root=self.workspace)
        self.assertTrue(policy.allows_write_to(self.workspace / "sub" / "a.txt"))
        self.assertFalse(policy.allows_write_to(Path("/etc/passwd")))

    def test_workspace_write_without_root_denies(self):
        policy = SandboxPolicy(mode=SandboxMode.WORKSPACE_WRITE, workspace_root=None)
        self.assertFalse(policy.allows_write_to(Path("/workspace/a.txt")))


class SandboxPolicyResolverTest(unittest.TestCase):
    def setUp(self):
        self.workspace = Path("/workspace")

    def test_default_mode_applied_when_no_override(self):
        resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.WORKSPACE_WRITE,
            workspace_root=self.workspace,
        )
        request = ExecutionRequest(tool_name="shell", arguments={}, actor="u1")
        policy = resolver.resolve(request)
        self.assertEqual(policy.mode, SandboxMode.WORKSPACE_WRITE)

    def test_session_override_beats_default(self):
        resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.WORKSPACE_WRITE,
            workspace_root=self.workspace,
            session_override=SandboxMode.READ_ONLY,
        )
        request = ExecutionRequest(tool_name="shell", arguments={}, actor="u1")
        policy = resolver.resolve(request)
        self.assertEqual(policy.mode, SandboxMode.READ_ONLY)

    def test_approved_escalation_wins(self):
        resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.WORKSPACE_WRITE,
            workspace_root=self.workspace,
        )
        request = ExecutionRequest(tool_name="shell", arguments={}, actor="u1")
        policy = resolver.resolve(request, escalation=SandboxMode.DANGER_FULL_ACCESS)
        self.assertEqual(policy.mode, SandboxMode.DANGER_FULL_ACCESS)

    def test_requested_mode_alone_does_not_escalate(self):
        # 工具请求更宽档位，但未经审批 —— 解析器不得自动提权。
        resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.WORKSPACE_WRITE,
            workspace_root=self.workspace,
        )
        request = ExecutionRequest(
            tool_name="shell",
            arguments={},
            actor="u1",
            requested_mode=SandboxMode.DANGER_FULL_ACCESS,
        )
        policy = resolver.resolve(request)
        self.assertEqual(policy.mode, SandboxMode.WORKSPACE_WRITE)


if __name__ == "__main__":
    unittest.main()
