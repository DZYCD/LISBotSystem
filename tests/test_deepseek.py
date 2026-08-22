"""DeepSeek 适配器测试：消息/工具格式转换 + 响应解析（用假 HTTP 服务器）。

用标准库 http.server 起一个假 API 端点，捕获请求体验证格式转换正确，
再返回构造的响应验证解析正确。不需要真实 API key。
"""

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from lis_harness.adapters import DeepSeekClient
from lis_harness.llm import LlmResult
from lis_harness.session import (
    Message,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def run(coro):
    return asyncio.run(coro)


class FakeAPIHandler(BaseHTTPRequestHandler):
    """捕获请求、返回可配置的响应。"""

    received_payload = None
    response_body = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        FakeAPIHandler.received_payload = json.loads(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(FakeAPIHandler.response_body).encode("utf-8"))

    def log_message(self, *args):
        pass


def start_fake_server():
    server = HTTPServer(("127.0.0.1", 0), FakeAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class DeepSeekClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = start_fake_server()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeAPIHandler.received_payload = None
        self.client = DeepSeekClient(api_key="test-key", base_url=self.base_url, model="deepseek-chat")

    def test_text_messages_converted_to_openai_format(self):
        FakeAPIHandler.response_body = {
            "choices": [{"message": {"role": "assistant", "content": "hi there"}}],
        }
        messages = [Message(role="user", content=[TextBlock(text="hello")])]
        result = run(self.client.generate(messages))
        # 请求体验证
        payload = FakeAPIHandler.received_payload
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertEqual(payload["messages"][0]["content"], "hello")
        # 响应解析
        self.assertIsInstance(result, LlmResult)
        self.assertEqual(result.blocks[0].text, "hi there")

    def test_tool_call_block_parsed(self):
        FakeAPIHandler.response_body = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command": "echo hi"}'},
                    }],
                },
            }],
        }
        messages = [Message(role="user", content=[TextBlock(text="run")])]
        result = run(self.client.generate(messages))
        self.assertEqual(len(result.blocks), 1)
        call = result.blocks[0]
        self.assertIsInstance(call, ToolCallBlock)
        self.assertEqual(call.name, "bash")
        self.assertIn("echo hi", call.arguments)

    def test_tool_result_message_converted_to_tool_role(self):
        FakeAPIHandler.response_body = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        messages = [
            Message(role="assistant", content=[
                ToolCallBlock(id="c1", name="bash", arguments="{}"),
            ]),
            Message(role="user", content=[
                ToolResultBlock(tool_call_id="c1", content="42"),
            ]),
        ]
        run(self.client.generate(messages))
        payload = FakeAPIHandler.received_payload
        self.assertEqual(payload["messages"][0]["role"], "assistant")
        self.assertIn("tool_calls", payload["messages"][0])
        # tool-result 消息被转成 role="tool"
        self.assertEqual(payload["messages"][1]["role"], "tool")
        self.assertEqual(payload["messages"][1]["tool_call_id"], "c1")
        self.assertEqual(payload["messages"][1]["content"], "42")

    def test_tools_included_in_payload(self):
        FakeAPIHandler.response_body = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
        messages = [Message(role="user", content=[TextBlock(text="x")])]
        tools = [{"name": "bash", "description": "run shell", "parameters": {"type": "object"}}]
        run(self.client.generate(messages, tools=tools))
        payload = FakeAPIHandler.received_payload
        self.assertEqual(payload["tools"][0]["function"]["name"], "bash")
        self.assertEqual(payload["tool_choice"], "auto")

    def test_missing_api_key_raises(self):
        import os
        old = os.environ.get("DEEPSEEK_API_KEY")
        os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            with self.assertRaises(Exception):
                DeepSeekClient(api_key="", base_url=self.base_url)
        finally:
            if old is not None:
                os.environ["DEEPSEEK_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
