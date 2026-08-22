"""注册中心 + 工具运行时测试：注册/发现/卸载/执行。"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from lis_harness.registry import (
    DisposedRegistryError,
    Registry,
    ToolCall,
    ToolDefinition,
    ToolRuntime,
)
from lis_harness.security import (
    ApprovalOutcome,
    CallbackApprovalService,
    ExecutionPipeline,
    SandboxMode,
    SandboxPolicyResolver,
)
from lis_harness.security.backends import JobObjectShell, JobSandboxConfig


def run(coro):
    return asyncio.run(coro)


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lis_harness_reg_")
        self.workspace = Path(self.tmp)
        self.registry = Registry()
        backend = JobObjectShell(JobSandboxConfig(timeout_ms=5000))
        self.dispose_backend = self.registry.register_backend("shell", backend)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_and_discover_tool(self):
        tool = ToolDefinition(
            name="bash",
            description="Run a shell command",
            parameters={"type": "object", "properties": {"command": {"type": "string"}}},
            backend="shell",
        )
        self.registry.register_tool(tool)
        found = self.registry.get_tool("bash")
        self.assertEqual(found.name, "bash")
        self.assertEqual(found.backend, "shell")

    def test_duplicate_tool_rejected(self):
        tool = ToolDefinition(name="bash", description="", parameters={}, backend="shell")
        self.registry.register_tool(tool)
        with self.assertRaises(ValueError):
            self.registry.register_tool(tool)

    def test_disposer_removes_tool(self):
        tool = ToolDefinition(name="bash", description="", parameters={}, backend="shell")
        dispose = self.registry.register_tool(tool)
        self.assertEqual(len(self.registry.list_tools()), 1)
        dispose()
        self.assertEqual(len(self.registry.list_tools()), 0)
        with self.assertRaises(DisposedRegistryError):
            self.registry.get_tool("bash")

    def test_disposed_backend_access_fails(self):
        backend = JobObjectShell()
        dispose = self.registry.register_backend("temp", backend)
        dispose()
        with self.assertRaises(DisposedRegistryError):
            self.registry.get_backend("temp")


class ToolRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lis_harness_runtime_")
        self.workspace = Path(self.tmp)
        self.registry = Registry()
        approval = CallbackApprovalService(lambda request, reason: ApprovalOutcome.ALLOWED_ONCE)

        resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.DANGER_FULL_ACCESS,
            workspace_root=self.workspace,
        )
        self.pipeline = ExecutionPipeline(
            policy_resolver=resolver,
            approval=approval,
        )
        self.runtime = ToolRuntime(self.registry, self.pipeline)

        # 注册能力后端 + 两个工具
        self.registry.register_backend("shell", JobObjectShell(JobSandboxConfig(timeout_ms=5000)))
        self.registry.register_tool(ToolDefinition(
            name="bash",
            description="Run a shell command",
            parameters={"type": "object"},
            backend="shell",
        ))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tool_call_executes_through_pipeline(self):
        result = run(self.runtime.execute(ToolCall(
            name="bash",
            arguments={"command": "echo hello"},
            actor="u1",
        )))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.value["exit_code"], 0)

    def test_tool_call_unknown_tool_fails(self):
        with self.assertRaises(KeyError):
            run(self.runtime.execute(ToolCall(name="nonexistent", arguments={}, actor="u1")))

    def test_workspace_write_via_tool(self):
        target = self.workspace / "tool.txt"
        result = run(self.runtime.execute(ToolCall(
            name="bash",
            arguments={"write": str(target), "content": "from tool"},
            actor="u1",
        )))
        self.assertTrue(result.ok, result.error)
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
