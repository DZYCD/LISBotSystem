"""长期记忆模块测试：token 估算、压缩沉淀知识库、查询、启动注入。"""

import asyncio
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tll_protocol_v2.memory import MemoryManager, estimate_tokens
from lis_harness.session import Session, TextBlock


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class MemoryManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_estimate_tokens(self):
        # 1 汉字≈2 token，1 英文≈0.5
        self.assertEqual(estimate_tokens("你好"), 4)
        self.assertEqual(estimate_tokens("hi"), 1)
        self.assertEqual(estimate_tokens(""), 0)

    def test_compress_no_llm_sinks_to_kb(self):
        session = Session()
        # 写入多条消息
        for i in range(6):
            session.append("user/message", {"content": [TextBlock(text=f"消息内容{i}")]})
            session.append("assistant/message", {"content": [TextBlock(text=f"回复{i}")]})
        mem = MemoryManager(bot_id="agent/test", base_dir=self.tmp, session=session, llm=None,
                            window_tokens=10, threshold=0.5)
        # 会话 token 应超过阈值
        self.assertTrue(mem.should_compress())
        points = run(mem.compress())
        self.assertTrue(len(points) > 0)  # 降级为按条摘要
        # 知识库应有沉淀
        self.assertEqual(len(mem.kb.get_all()), len(points))

    def test_query_finds_knowledge(self):
        mem = MemoryManager(bot_id="agent/test", base_dir=self.tmp, llm=None)
        mem.kb.add(content="LIS 集群的密码是 LIS-2026", keywords=["密码", "LIS"])
        mem.kb.add(content="MQTT 用 broker.emqx.io", keywords=["MQTT"])
        results = mem.query("密码")
        self.assertTrue(len(results) >= 1)
        self.assertIn("LIS-2026", results[0]["content"])

    def test_recall_injects_context(self):
        mem = MemoryManager(bot_id="agent/test", base_dir=self.tmp, llm=None)
        mem.kb.add(content="部署端口是 3080", keywords=["端口"])
        recall = mem.recall("端口")
        self.assertIn("部署端口是 3080", recall)
        # 无匹配时返回空
        self.assertEqual(mem.recall("不存在的词"), "")

    def test_rag_mixed_retrieval_finds_semantic_match(self):
        """RAG 增强：向量语义检索能找到字面不完全相同但相关的记忆。"""
        mem = MemoryManager(bot_id="agent/test", base_dir=self.tmp, llm=None)
        mem.kb.add(content="徐州天气：阴天 26 度，湿度 89%", keywords=["徐州", "天气"])
        mem.kb.add(content="部署端口是 3080", keywords=["端口"])
        # 查询"徐州气温"（语义相关但字面不完全匹配"天气"）
        results = mem.query("徐州气温", top_k=3)
        self.assertTrue(len(results) >= 1, f"RAG 应检索到相关记忆，got {results}")
        # 应命中徐州天气那条（向量语义匹配）
        contents = [r.get("content", "") for r in results]
        self.assertTrue(any("徐州" in c for c in contents), f"应命中徐州天气，got {contents}")

    def test_rag_falls_back_to_keyword(self):
        """RAG 索引为空/无匹配时不影响（仍是可用查询，无异常）。"""
        mem = MemoryManager(bot_id="agent/test", base_dir=self.tmp, llm=None)
        mem.kb.add(content="你好世界", keywords=["问候"])
        # 完全无关查询返回空（不崩溃）
        results = mem.query("完全无关的随机词xyz", top_k=5)
        self.assertEqual(results, [])


class MockKnowledgeLLM:
    """模拟 LLM，generate_knowledge_points 返回固定知识点。"""

    async def generate_knowledge_points(self, messages):
        return [{
            "topic": "测试",
            "summary": "关键事实：LIS-2026",
            "keywords": ["LIS", "测试"],
            "source": "llm_compress",
        }]


class MemoryWithLLMTest(unittest.TestCase):
    def test_compress_with_llm_sinks_knowledge(self):
        tmp = tempfile.mkdtemp()
        session = Session()
        for i in range(4):
            session.append("user/message", {"content": [TextBlock(text=f"问题{i}")]})
        mem = MemoryManager(bot_id="agent/t", base_dir=tmp, session=session,
                            llm=MockKnowledgeLLM(), window_tokens=1, threshold=0.5)
        points = run(mem.compress())
        self.assertEqual(points[0]["topic"], "测试")
        self.assertIn("LIS-2026", points[0]["summary"])
        # 沉淀进知识库
        self.assertEqual(len(mem.kb.get_all()), 1)


if __name__ == "__main__":
    unittest.main()
