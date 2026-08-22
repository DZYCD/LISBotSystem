"""TLL transport 整改测试：结构对齐真实 Task + command 能力校验。"""

import asyncio
import unittest

from lis_harness.adapters import TLLTransport, TLLTransportConfig
from lis_harness.security import ExecutionRequest, SandboxMode, SandboxPolicy


def run(coro):
    return asyncio.run(coro)


def _danger_policy():
    return SandboxPolicy(mode=SandboxMode.DANGER_FULL_ACCESS, workspace_root=None)


def _req(args, actor="u1"):
    return ExecutionRequest(tool_name="tll", arguments=args, actor=actor)


class TLLTaskStructureTest(unittest.TestCase):
    def test_to_dict_matches_real_task_structure(self):
        from lis_harness.adapters.tll_transport import TLLTask
        task = TLLTask(
            task_id="abc123",
            from_bot="agent/eiar_001",
            to="agent/sayi_996",
            command="web_search",
            params={"query": "x"},
        )
        d = task.to_dict()
        # 关键字段对齐真实 Task.to_dict()
        self.assertEqual(d["id"], "abc123")
        self.assertEqual(d["from_bot"], "agent/eiar_001")
        # 命令载荷嵌套在 tlljson
        self.assertEqual(d["tlljson"]["from_bot"], "agent/eiar_001")
        self.assertEqual(d["tlljson"]["command"], "web_search")
        self.assertEqual(d["tlljson"]["to"], "agent/sayi_996")
        self.assertEqual(d["tlljson"]["params"], {"query": "x"})
        # created_at 是 ISO-8601 字符串
        self.assertIsInstance(d["created_at"], str)
        self.assertIn("T", d["created_at"])  # ISO-8601 含 T 分隔符
        # 状态字段
        self.assertEqual(d["status"], "pending")
        self.assertEqual(d["type"], "tool")


class TLLTransportGovernanceTest(unittest.TestCase):
    def setUp(self):
        # peers 声明了 sayi_996 的能力（tools 白名单）
        self.tll = TLLTransport(TLLTransportConfig(
            my_bot_id="agent/eiar_001",
            peers={
                "agent/sayi_996": {
                    "tools": [{"name": "web_search"}, {"name": "summarize"}],
                },
            },
        ))

    def test_command_within_peer_tools_allowed(self):
        result = run(self.tll.execute(
            _req({"to": "agent/sayi_996", "command": "web_search", "params": {"query": "x"}}),
            _danger_policy(),
        ))
        self.assertTrue(result.ok, result.error)

    def test_command_outside_peer_tools_denied(self):
        # A4 修复：command 不在 peer 声明能力内 → 拒绝
        result = run(self.tll.execute(
            _req({"to": "agent/sayi_996", "command": "delete_all", "params": {}}),
            _danger_policy(),
        ))
        self.assertFalse(result.ok)
        self.assertTrue(result.denied)
        self.assertIn("not in", result.error)
        self.assertIn("delete_all", result.error)

    def test_unknown_peer_denied(self):
        result = run(self.tll.execute(
            _req({"to": "agent/evil", "command": "hack", "params": {}}),
            _danger_policy(),
        ))
        self.assertFalse(result.ok)
        self.assertTrue(result.denied)

    def test_peer_without_tools_allows_any_command(self):
        # peers 无 tools 声明时（allowed_names 为空）→ 不限制 command
        tll = TLLTransport(TLLTransportConfig(
            my_bot_id="agent/eiar_001",
            peers={"agent/other": {}},
        ))
        result = run(tll.execute(
            _req({"to": "agent/other", "command": "anything", "params": {}}),
            _danger_policy(),
        ))
        self.assertTrue(result.ok, result.error)

    def test_sent_task_has_correct_structure(self):
        run(self.tll.execute(
            _req({"to": "agent/sayi_996", "command": "web_search", "params": {"query": "y"}}),
            _danger_policy(),
        ))
        self.assertEqual(len(self.tll.sent_tasks), 1)
        task = self.tll.sent_tasks[0]
        d = task.to_dict()
        self.assertEqual(d["tlljson"]["to"], "agent/sayi_996")
        self.assertEqual(d["tlljson"]["command"], "web_search")


if __name__ == "__main__":
    unittest.main()
