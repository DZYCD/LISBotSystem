"""JobObjectShell 后端测试：真实子进程、命令路径沙箱、超时终止、路径范围、输出捕获。"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from lis_harness.security import (
    ExecutionRequest,
    SandboxMode,
    SandboxPolicy,
)
from lis_harness.security.backends import JobObjectShell, JobSandboxConfig


def run(coro):
    return asyncio.run(coro)


class JobObjectShellTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lis_harness_job_")
        self.workspace = Path(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _req(self, args, actor="u1"):
        return ExecutionRequest(tool_name="shell", arguments=args, actor=actor)

    def _danger_policy(self):
        return SandboxPolicy(mode=SandboxMode.DANGER_FULL_ACCESS, workspace_root=None)

    # --- 命令路径沙箱（整改核心） ---

    def test_command_denied_outside_danger_mode(self):
        # 命令在非 DANGER 档下必须被拒绝（当前环境无法 OS 级隔离命令路径）
        shell = JobObjectShell()
        policy = SandboxPolicy(mode=SandboxMode.WORKSPACE_WRITE, workspace_root=self.workspace)
        result = run(shell.execute(self._req({"command": "echo hello"}), policy))
        self.assertFalse(result.ok)
        self.assertTrue(result.denied)
        self.assertIn("danger-full-access", result.error)

    def test_command_denied_in_read_only(self):
        shell = JobObjectShell()
        policy = SandboxPolicy(mode=SandboxMode.READ_ONLY, workspace_root=self.workspace)
        result = run(shell.execute(
            self._req({"command": f"python -c \"open({str(Path(self.workspace)/'pwn.txt')!r},'w').write('x')\""}),
            policy,
        ))
        self.assertFalse(result.ok)
        self.assertTrue(result.denied)

    def test_real_command_runs_in_danger_mode(self):
        shell = JobObjectShell()
        result = run(shell.execute(self._req({"command": "echo hello"}), self._danger_policy()))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.value["exit_code"], 0)

    def test_command_exit_code_propagates(self):
        shell = JobObjectShell()
        result = run(shell.execute(
            self._req({"command": "python -c \"import sys; sys.exit(7)\""}),
            self._danger_policy(),
        ))
        self.assertFalse(result.ok)
        self.assertIn("7", result.error)

    def test_command_output_captured(self):
        # 整改：命令输出必须被捕获回传到 result（模型能看到）
        shell = JobObjectShell()
        result = run(shell.execute(
            self._req({"command": "echo HELLO_CAPTURE"}),
            self._danger_policy(),
        ))
        self.assertTrue(result.ok, result.error)
        self.assertIn("HELLO_CAPTURE", result.value.get("stdout", ""))

    def test_timeout_terminates_process(self):
        shell = JobObjectShell(JobSandboxConfig(timeout_ms=1000))
        result = run(shell.execute(
            self._req({"command": "python -c \"import time; time.sleep(60)\""}),
            self._danger_policy(),
        ))
        self.assertFalse(result.ok)
        self.assertTrue(result.denied)
        self.assertIn("timeout", result.error)

    # --- 路径范围（write/read） ---

    def test_write_inside_workspace_succeeds(self):
        shell = JobObjectShell()
        policy = SandboxPolicy(mode=SandboxMode.WORKSPACE_WRITE, workspace_root=self.workspace)
        target = self.workspace / "a.txt"
        result = run(shell.execute(
            self._req({"write": str(target), "content": "hello"}),
            policy,
        ))
        self.assertTrue(result.ok, result.error)
        self.assertTrue(target.exists())

    def test_write_outside_workspace_denied(self):
        shell = JobObjectShell()
        policy = SandboxPolicy(mode=SandboxMode.WORKSPACE_WRITE, workspace_root=self.workspace)
        outside = Path(self.workspace).parent / "outside.txt"
        result = run(shell.execute(
            self._req({"write": str(outside), "content": "x"}),
            policy,
        ))
        self.assertFalse(result.ok)
        self.assertTrue(result.denied)
        self.assertFalse(outside.exists())

    def test_read_inside_workspace_succeeds(self):
        shell = JobObjectShell()
        policy = SandboxPolicy(mode=SandboxMode.WORKSPACE_WRITE, workspace_root=self.workspace)
        target = self.workspace / "a.txt"
        target.write_text("data", encoding="utf-8")
        result = run(shell.execute(self._req({"read": str(target)}), policy))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.value["content"], "data")

    def test_read_outside_workspace_denied(self):
        # 整改：read 也走范围检查，防止任意读系统文件
        shell = JobObjectShell()
        policy = SandboxPolicy(mode=SandboxMode.WORKSPACE_WRITE, workspace_root=self.workspace)
        result = run(shell.execute(self._req({"read": "C:/Windows/win.ini"}), policy))
        self.assertFalse(result.ok)
        self.assertTrue(result.denied)

    def test_read_allowed_in_danger_mode(self):
        shell = JobObjectShell()
        result = run(shell.execute(self._req({"read": "C:/Windows/win.ini"}), self._danger_policy()))
        # DANGER 档允许任意读
        self.assertTrue(result.ok, result.error)


if __name__ == "__main__":
    unittest.main()
