"""TLL v2 核心测试：线协议契约 + 加密 + 线程安全回传。"""

import asyncio
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tll_protocol_v2.core import Task, TLLjson, TaskStatus, Trace, TraceHop
from tll_protocol_v2.security import encrypt_payload, decrypt_payload


class CoreContractTest(unittest.TestCase):
    def test_task_to_dict_roundtrip(self):
        t = Task(
            task_type="general", from_bot="agent/a", current_agent="agent/a",
            tlljson=TLLjson(from_bot="agent/a", command="chat", to="agent/b", params={"text": "hi"}),
        )
        t.route.append("agent/a")
        t.delegate_count = 1
        d = t.to_dict()
        # 关键字段
        self.assertEqual(d["from_bot"], "agent/a")
        self.assertEqual(d["tlljson"]["command"], "chat")
        self.assertEqual(d["tlljson"]["params"], {"text": "hi"})
        self.assertEqual(d["status"], "pending")
        self.assertEqual(d["route"], ["agent/a"])
        self.assertIsInstance(d["created_at"], str)
        self.assertIn("T", d["created_at"])  # ISO-8601
        # roundtrip
        t2 = Task.from_dict(d)
        self.assertEqual(t2.id, t.id)
        self.assertEqual(t2.tlljson.command, "chat")
        self.assertEqual(t2.route, ["agent/a"])
        self.assertEqual(t2.delegate_count, 1)

    def test_task_status_values(self):
        self.assertEqual(TaskStatus.PENDING.value, "pending")
        self.assertEqual(TaskStatus.DELEGATED.value, "delegated")
        self.assertEqual(TaskStatus.RETURNING.value, "returning")
        self.assertEqual(TaskStatus.CHECK_REVIEW.value, "check_review")

    def test_trace_roundtrip(self):
        tr = Trace()
        tr.add_hop("agent/a", "continue_to_agent/b")
        d = tr.to_dict()
        self.assertEqual(d["hops"][0]["bot"], "agent/a")
        tr2 = Trace.from_dict(d)
        self.assertEqual(tr2.hops[0].action, "continue_to_agent/b")


class SecurityTest(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip(self):
        data = b'{"type":"TASK","task":{}}'
        enc = encrypt_payload(data, "sk-key")
        self.assertNotEqual(enc, data)  # 加密了
        dec = decrypt_payload(enc, "sk-key")
        self.assertEqual(dec, data)

    def test_empty_key_passthrough(self):
        data = b"plaintext"
        self.assertEqual(encrypt_payload(data, ""), data)
        self.assertEqual(decrypt_payload(data, ""), data)

    def test_wrong_key_returns_original(self):
        data = b"hello"
        enc = encrypt_payload(data, "sk-1")
        dec = decrypt_payload(enc, "sk-2")  # 错误 key
        self.assertNotEqual(dec, data)  # 解密失败返回密文本身


class ThreadSafeReturnTest(unittest.TestCase):
    def test_handle_response_threadsafe_wakes_future(self):
        from tll_protocol_v2.transport import V2TLLTransport
        from tll_protocol_v2.mqtt import MQTTTransport, MQTTConfig
        from lis_harness.adapters import TLLTransportConfig

        # 构造 V2TLLTransport（handle_response 是线程安全的）
        mqtt = MQTTTransport(MQTTConfig(client_id="t"))
        tll = V2TLLTransport(
            TLLTransportConfig(my_bot_id="agent/t", peers={"agent/b": {"tools": []}}, timeout_s=3),
            mqtt, "agent/t",
        )

        async def scenario():
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            tll._pending["tid-1"] = fut
            def respond():
                consumed = tll.handle_response("tid-1", {"ok": True})
                assert consumed, "should consume"
            import threading
            threading.Thread(target=respond, daemon=True).start()
            result = await asyncio.wait_for(fut, timeout=2)
            return result

        result = asyncio.run(scenario())
        self.assertEqual(result, {"ok": True})

    def test_handle_response_not_consumed_when_no_pending(self):
        from tll_protocol_v2.transport import V2TLLTransport
        from tll_protocol_v2.mqtt import MQTTTransport, MQTTConfig
        from lis_harness.adapters import TLLTransportConfig
        mqtt = MQTTTransport(MQTTConfig(client_id="t"))
        tll = V2TLLTransport(TLLTransportConfig(my_bot_id="agent/t", peers={}, timeout_s=3), mqtt, "agent/t")
        self.assertFalse(tll.handle_response("unknown-id", {"x": 1}))


if __name__ == "__main__":
    unittest.main()
