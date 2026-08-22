"""插件加载器测试：YAML 加载 + 调用前热重载。"""

import os
import shutil
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from lis_harness.loader import PluginLoader
from lis_harness.registry import Registry, ToolCall, ToolRuntime
from lis_harness.security import (
    ApprovalOutcome,
    CallbackApprovalService,
    ExecutionPipeline,
    SandboxMode,
    SandboxPolicyResolver,
)
from lis_harness.security.backends import JobObjectShell, JobSandboxConfig

WORKSPACE = "lis_harness"  # 项目根，作为工作区（loader 的 base_dir 用它解析实现文件）


class LoaderTest(unittest.TestCase):
    def setUp(self):
        # 使用项目真实目录（bash_tool.py / jobshell.py 在那里）
        self.tmp = tempfile.mkdtemp(prefix="lis_harness_loader_")
        self.base = Path(".").resolve()
        self.registry = Registry()
        self.loader = PluginLoader(self.registry, self.base)

        # 后端直接注册（不通过 YAML 监视，focus 工具热重载）
        self.registry.register_backend("shell", JobObjectShell(JobSandboxConfig(timeout_ms=5000)))

        # 解析到真实 bash_tool.py 路径
        self.tool_file = Path(self.base) / "lis_harness" / "tools" / "bash_tool.py"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        # 恢复可能被测试修改的实现文件
        import importlib
        try:
            importlib.invalidate_caches()
        except Exception:
            pass

    def _write_yaml(self, implements):
        yaml_path = Path(self.tmp) / "tools.yaml"
        yaml_path.write_text(textwrap.dedent(f"""
            tools:
              bash:
                implements: {implements}
                backend: shell
        """), encoding="utf-8")
        return yaml_path

    def test_load_registers_tool_from_yaml(self):
        yaml_path = self._write_yaml("lis_harness.tools.bash_tool")
        self.loader.load(yaml_path)
        tool = self.registry.get_tool("bash")
        self.assertEqual(tool.name, "bash")
        self.assertEqual(tool.backend, "shell")

    def test_reload_if_changed_detects_and_reloads(self):
        yaml_path = self._write_yaml("lis_harness.tools.bash_tool")
        self.loader.load(yaml_path)
        # 初始未变化
        self.assertFalse(self.loader.reload_if_changed("bash"))
        # 改写源文件并确保 mtime 变化
        original = self.tool_file.read_text(encoding="utf-8")
        modified = original + "\n# reload marker\n"
        self.tool_file.write_text(modified, encoding="utf-8")
        # mtime 精度问题：强制更新 mtime
        os.utime(self.tool_file, None)
        # reload 需要重新执行模块；先确认检测到变化
        self.assertTrue(self.loader.reload_if_changed("bash"))
        # 恢复源文件
        self.tool_file.write_text(original, encoding="utf-8")

    def test_reload_replaces_tool_registration(self):
        yaml_path = self._write_yaml("lis_harness.tools.bash_tool")
        self.loader.load(yaml_path)
        before = self.registry.get_tool("bash")
        # 改写 + 触碰 mtime + 重载
        original = self.tool_file.read_text(encoding="utf-8")
        try:
            self.tool_file.write_text(original + "\n# version2\n", encoding="utf-8")
            os.utime(self.tool_file, None)
            self.loader.reload_tool("bash")
            after = self.registry.get_tool("bash")
            # 注册中心里仍然是同一个名字的工具（新实例）
            self.assertEqual(after.name, "bash")
            self.assertIsNot(before, after)
        finally:
            self.tool_file.write_text(original, encoding="utf-8")


class LoaderRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lis_harness_loader_runtime_")
        self.base = Path(".").resolve()
        self.registry = Registry()
        approval = CallbackApprovalService(lambda r, reason: ApprovalOutcome.ALLOWED_ONCE)
        resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.DANGER_FULL_ACCESS,
            workspace_root=self.base,
        )
        self.pipeline = ExecutionPipeline(policy_resolver=resolver, approval=approval)
        self.loader = PluginLoader(self.registry, self.base)
        self.registry.register_backend("shell", JobObjectShell(JobSandboxConfig(timeout_ms=5000)))
        self.runtime = ToolRuntime(self.registry, self.pipeline, reload_hook=self.loader.reload_if_changed)

        yaml_path = Path(self.tmp) / "tools.yaml"
        yaml_path.write_text(textwrap.dedent("""
            tools:
              bash:
                implements: lis_harness.tools.bash_tool
                backend: shell
        """), encoding="utf-8")
        self.loader.load(yaml_path)
        self.tool_file = Path(self.base) / "lis_harness" / "tools" / "bash_tool.py"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_execute_via_loader_registered_tool(self):
        import asyncio
        result = asyncio.run(self.runtime.execute(ToolCall(
            name="bash", arguments={"command": "echo loaded"}, actor="u1")))
        self.assertTrue(result.ok, result.error)


if __name__ == "__main__":
    unittest.main()
