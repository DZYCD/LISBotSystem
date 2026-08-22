"""会话日志测试：append-only、derive_messages、持久化/回放。"""

import unittest

from lis_harness.session import (
    Message,
    Session,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)


class SessionTest(unittest.TestCase):
    def test_append_assigns_monotonic_seq(self):
        s = Session()
        e1 = s.append("turn/start", {"turn": 1})
        e2 = s.append("user/message", {"content": [TextBlock(text="hi")]})
        self.assertEqual(e1.seq, 0)
        self.assertEqual(e2.seq, 1)
        self.assertEqual(s.seq, 2)

    def test_derive_messages_projects_log(self):
        s = Session()
        s.append("turn/start", {"turn": 1})
        s.append("user/message", {"content": [TextBlock(text="hi")]})
        s.append("assistant/message", {"content": [TextBlock(text="hello")]})
        s.append("tool/result", {
            "content": ToolResultBlock(tool_call_id="c1", content="42"),
        })
        msgs = s.derive_messages()
        # turn/start 不投影；user、assistant、tool/result 依次投影
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[0].role, "user")
        self.assertEqual(msgs[1].role, "assistant")
        self.assertEqual(msgs[2].role, "user")  # tool/result 是 user 角色
        self.assertEqual(msgs[2].content[0].type, "tool-result")

    def test_dump_restore_roundtrip(self):
        s = Session("s1")
        s.append("user/message", {"content": [TextBlock(text="hi")]})
        s.append("assistant/message", {"content": [ToolCallBlock(id="c1", name="bash", arguments="{}")]})
        dumped = s.dump()
        restored = Session.restore(dumped)
        self.assertEqual(restored.session_id, "s1")
        self.assertEqual(restored.seq, s.seq)
        restored_msgs = restored.derive_messages()
        self.assertEqual(len(restored_msgs), 2)
        self.assertEqual(restored_msgs[1].content[0].type, "tool-call")


if __name__ == "__main__":
    unittest.main()
