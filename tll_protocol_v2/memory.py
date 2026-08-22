"""v2 长期记忆模块：复用旧 KnowledgeBase（知识沉淀）+ harness Session（会话）+ LLM 压缩。

机制（对齐旧 tll_protocol 的记忆设计，但适配 harness 的 append-only Session）：
- 会话真相源仍是 harness Session（append-only 日志）。
- MemoryManager 绑定一个 KnowledgeBase（knowledge_base.json，长期记忆）和 LLM。
- 当会话消息量/估算 token 达到阈值，触发 compress()：调 LLM 把旧消息拆成
  知识点 → kb.add_from_summary() 沉淀进知识库 + 写摘要文件。
- 启动时可调用 recall(seed) 把相关知识点注入 system 上下文（跨会话记忆）。
- LLM 可通过 memory_query 工具自主查询知识库。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 复用旧 tll_protocol 的 KnowledgeBase（同一份实现，不重复造）
_OLD_TLL = Path(__file__).resolve().parent.parent / "tll_protocol"
if str(_OLD_TLL) not in sys.path:
    sys.path.insert(0, str(_OLD_TLL))
from knowledge_base import KnowledgeBase, create_knowledge_base  # noqa: E402


def estimate_tokens(text: str) -> int:
    """保守估算 token 数：1汉字≈2token，1英文≈0.5token（对齐旧实现）。"""
    if not text:
        return 0
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - chinese
    return int(chinese * 2 + other * 0.5)


class MemoryManager:
    """v2 长期记忆管理器。"""

    def __init__(
        self,
        bot_id: str,
        base_dir: str,
        session=None,
        llm=None,
        window_tokens: int = 200_000,
        threshold: float = 0.8,
    ) -> None:
        self.bot_id = bot_id
        self.base_dir = base_dir
        self.session = session            # harness Session（会话真相源）
        self.llm = llm                    # LlmClient（async）
        self.window_tokens = window_tokens
        self.threshold = threshold
        self.kb = create_knowledge_base(base_dir, bot_id)
        self.summaries_dir = os.path.join(base_dir, "summaries")
        os.makedirs(self.summaries_dir, exist_ok=True)
        # RAG 增强检索引擎（懒构建）：向量化知识库 + 语义检索
        self._rag = None

    def _ensure_rag(self):
        """懒构建 RAG 引擎（首次查询时）。用 knowledge_base 条目向量化建索引。

        优先用 RemoteEmbedder（BGE 语义向量，真实 RAG）；服务不可用时退回
        内置轻量词袋向量，再退纯关键词。
        """
        if self._rag is not None:
            return self._rag
        embedder = None
        # 尝试连 BGE embedding 服务（可用则用真实语义向量）
        try:
            from .rag import RemoteEmbedder
            probe = RemoteEmbedder()
            probe.embed("探")  # 探测服务是否在线（会缓存）
            embedder = probe
        except Exception:
            embedder = None
        try:
            from .rag import RAGEngine
            items = [it.to_dict() for it in self.kb.items.values()]
            rag = RAGEngine(embedder=embedder)
            rag.index(items)
            self._rag = rag
        except Exception:
            self._rag = False  # 构建失败则退回纯关键词
        return self._rag

    # --- 会话 token 统计 ---

    def session_tokens(self) -> int:
        """估算当前会话的 token 数（从 Session 事件投影）。"""
        if self.session is None:
            return 0
        total = 0
        for ev in self.session.events:
            if ev.type in ("user/message", "assistant/message"):
                for block in ev.data.get("content", []):
                    total += estimate_tokens(getattr(block, "text", ""))
            elif ev.type == "tool/result":
                content = ev.data.get("content", "")
                # content 可能是 ToolResultBlock 对象或字符串
                if hasattr(content, "content"):
                    content = content.content
                total += estimate_tokens(content if isinstance(content, str) else "")
        return total

    def should_compress(self) -> bool:
        return self.session_tokens() >= self.window_tokens * self.threshold

    # --- 压缩：会话 → 知识点 → 知识库 ---

    async def compress(self) -> List[Dict]:
        """压缩会话前半，把旧消息拆成知识点存进知识库。

        返回生成的知识点列表。若 LLM 不可用则降级为按条摘要。
        """
        if self.session is None:
            return []
        msgs = self._derive_messages_for_compress()
        if len(msgs) < 2:
            return []

        old = msgs[: len(msgs) // 2]
        points = await self._generate_knowledge_points(old)
        if not points:
            return []

        # 写摘要文件
        summary = {
            "timestamp": datetime.now().isoformat(),
            "bot_id": self.bot_id,
            "session_id": getattr(self.session, "session_id", ""),
            "old_message_count": len(old),
            "knowledge_points": points,
        }
        summary_file = os.path.join(
            self.summaries_dir,
            f'{datetime.now().strftime("%Y%m%d_%H%M%S")}.json',
        )
        with open(summary_file, "w", encoding="utf-8") as f:
            import json as _json
            _json.dump(summary, f, ensure_ascii=False, indent=2)

        # 沉淀进知识库
        self.kb.add_from_summary(summary, source_bot=self.bot_id)
        return points

    def _derive_messages_for_compress(self) -> List[Dict]:
        """把 Session 事件投影为 (role, content) 消息列表，供压缩。"""
        msgs = []
        for ev in self.session.events:
            if ev.type == "user/message":
                text = "".join(getattr(b, "text", "") for b in ev.data.get("content", []))
                if text:
                    msgs.append({"role": "user", "content": text})
            elif ev.type == "assistant/message":
                text = "".join(getattr(b, "text", "") for b in ev.data.get("content", []))
                if text:
                    msgs.append({"role": "assistant", "content": text})
            elif ev.type == "tool/result":
                content = ev.data.get("content", "")
                if hasattr(content, "content"):
                    content = content.content
                msgs.append({"role": "tool", "content": content if isinstance(content, str) else ""})
        return msgs

    async def _generate_knowledge_points(self, messages: List[Dict]) -> List[Dict]:
        """调 LLM 把旧消息拆成知识点；无 LLM 则降级为按条摘要。"""
        if self.llm is None:
            return self._fallback_points(messages)
        try:
            return await self.llm.generate_knowledge_points(messages)
        except Exception:
            return self._fallback_points(messages)

    def _fallback_points(self, messages: List[Dict]) -> List[Dict]:
        points = []
        for m in messages:
            if m.get("content"):
                points.append({
                    "topic": "通用",
                    "summary": m["content"][:200],
                    "keywords": [m.get("role", "")],
                    "source": "auto_summary",
                })
        return points

    # --- 记忆查询（供 LLM 工具 + 启动注入） ---

    def query(self, text: str, top_k: int = 5) -> List[Dict]:
        """查询知识库（RAG 增强：向量语义 + 关键词混合检索）。

        优先走 RAG 检索引擎；无向量索引或构建失败时退回纯关键词搜索。
        """
        rag = self._ensure_rag()
        if rag:
            try:
                items = rag.search(text, top_k=top_k)
                if items:
                    return items
            except Exception:
                pass
        return self.kb.search(text, top_k=top_k)

    def query_by_keyword(self, text: str) -> List[Dict]:
        return self.kb.search_by_keyword_hit(text)

    def recall(self, seed: str, top_k: int = 3) -> str:
        """把相关知识点拼成一段可注入 system 的文本（跨会话记忆）。"""
        items = self.query(seed, top_k=top_k)
        if not items:
            return ""
        lines = ["【长期记忆】以下是从知识库检索到的相关记忆："]
        for it in items:
            lines.append(f"- {it.get('content', '')} (触发词: {', '.join(it.get('keywords', []) or [])})")
        return "\n".join(lines)
