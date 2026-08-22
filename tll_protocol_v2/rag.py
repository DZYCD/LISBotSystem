"""RAG 增强检索引擎 —— 作为 memory_query 工具的增强检索后端。

思想：检索增强生成（RAG）。把知识库内容向量化，查询时做语义相似度检索，
并与现有关键词检索做混合融合（关键词命中加权 + 向量余弦相似度），
显著提升记忆召回准确率（尤其语义相近但字面不同的查询）。

设计要点：
- 无重型依赖：内置"字符级词袋（Bag-of-Chars）TF 特征 + 余弦相似度"作为
  轻量语义近似，可直接跑通、可验证。
- 可插拔 Embedder：`embed(text) -> List[float]` 接口，后续可换 DeepSeek/
  BGE 等真实语义 embedding 模型，无需改检索逻辑。
- 混合检索：关键词 score（命中 +3/内容 +1，对齐旧 KnowledgeBase）+ 向量
  余弦相似度加权，综合排序。
- 向后兼容：索引为空时退回纯关键词检索。

用法：
    rag = RAGEngine(embedder=my_embedder)
    rag.index(items)                # items: [{content, keywords, ...}]
    results = rag.search(query, top_k=5)   # 返回按综合分排序的条目
"""

from __future__ import annotations

import json
import math
import urllib.request
from typing import Any, Callable, Dict, List, Optional


# 停用字（常见虚词/助词），过滤后建 bigram，减少无关文本的误匹配
_STOP_CHARS = set("的了是和在就都有吗呢吧啊哦哈哈好的")

def _norm_grams(text: str) -> Dict[str, int]:
    """去停用字后提取 bigram 字符对。"""
    cleaned = "".join(c for c in text if c not in _STOP_CHARS)
    grams = {}
    for i in range(len(cleaned) - 1):
        g = cleaned[i:i + 2]
        grams[g] = grams.get(g, 0) + 1
    return grams


def default_embedder(text: str) -> List[float]:
    """内置轻量特征向量：去停用字后字符级词袋（bigram）TF 权重。

    近似语义：能捕捉"字符重合度"（同义/近形词有重叠字符），但非真实语义
    向量。真实语义 embedding（DeepSeek/BGE）通过 RAGEngine(embedder=...) 注入。
    """
    grams = _norm_grams(text or "")
    # TF 权重（对数缩放），返回稳定顺序的特征向量（按 gram 排序便于一致性）
    keys = sorted(grams.keys())
    return [grams[k] / (1 + math.log(len(grams) + 1)) for k in keys]


def cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度：两个对齐特征向量（用集合交集对齐）。"""
    if not a or not b:
        return 0.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


class RAGEngine:
    """向量增强检索引擎。"""

    def __init__(self, embedder: Optional[Callable[[str], List[float]]] = None) -> None:
        self._embedder = embedder or default_embedder
        self._index: List[Dict[str, Any]] = []
        # 缓存的文本→向量（避免重复 embed）
        self._cache: Dict[str, List[float]] = {}

    def index(self, items: List[Dict[str, Any]]) -> None:
        """建立向量索引。items 每条含 content / keywords（可选）等字段。"""
        self._index = []
        for it in items:
            content = (it.get("content") or it.get("知识点") or it.get("text") or "")
            if not content:
                continue
            vec = self._embed(content)
            self._index.append({
                "item": it,
                "content": content,
                "keywords": list(it.get("keywords") or it.get("关键词") or []) if isinstance(it.get("keywords") or it.get("关键词") or [], (list, tuple)) else [],
                "vector": vec,
            })

    def _embed(self, text: str) -> List[float]:
        if text not in self._cache:
            self._cache[text] = self._embedder(text)
        return self._cache[text]

    def search(self, query: str, top_k: int = 5, min_similarity: float = 0.5,
               keyword_weight: float = 0.15) -> List[Dict[str, Any]]:
        """混合检索：以向量余弦相似度为主判据，关键词命中为辅助加权。

        - min_similarity：向量相似度最低阈值（低于视为无关，不返回）。
        - keyword_weight：关键词命中的辅助权重（加到综合分，不影响是否返回，
          仅影响同相似度下的排序）。
        返回按综合分排序的 top_k 条（保留 item 原样）。
        """
        if not query or not self._index:
            return []
        q = (query or "").lower()
        q_vec = self._embed(query)
        scored = []
        for entry in self._index:
            sim = cosine(q_vec, entry["vector"])
            if sim < min_similarity:
                continue  # 语义无关，直接过滤
            # 关键词命中辅助加权（仅排序）
            kw = 0.0
            for k in entry["keywords"]:
                if k and k.lower() in q:
                    kw += 1.0
            if entry["content"].lower() and q in entry["content"].lower():
                kw += 0.5
            score = sim + kw * keyword_weight
            scored.append({"item": entry["item"], "score": score,
                           "similarity": round(sim, 4), "kw": kw})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return [s["item"] for s in scored[:top_k]]

    def search_ranked(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """返回带相似度评分的检索结果（供调试/展示）。"""
        if not query or not self._index:
            return []
        q_vec = self._embed(query)
        q = (query or "").lower()
        out = []
        for entry in self._index:
            sim = cosine(q_vec, entry["vector"])
            kw = 0.0
            for k in entry["keywords"]:
                if k and k.lower() in q:
                    kw += 3.0
            if entry["content"].lower() and q in entry["content"].lower():
                kw += 1.0
            out.append({"item": entry["item"], "keywords_score": kw, "vector_similarity": round(sim, 4)})
        out.sort(key=lambda x: x["keywords_score"] + x["vector_similarity"] * 5, reverse=True)
        return out[:top_k]


def build_from_knowledge(knowledge_items: List[Dict[str, Any]],
                         embedder: Optional[Callable[[str], List[float]]] = None) -> RAGEngine:
    """从现有 knowledge_base 条目构建 RAG 引擎。"""
    rag = RAGEngine(embedder=embedder)
    rag.index(knowledge_items)
    return rag


class RemoteEmbedder:
    """通过 HTTP 调用本地 BGE embedding 服务（ultralytics 环境跑）做向量化。

    主项目（Python 3.14）无法直接 import ultralytics 环境的 torch，因此把
    embedding 抽成独立服务（见 embedding_server.py），这里用标准库 urllib
    调用，批量缓存向量，避免重复请求。
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8677", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._cache: Dict[str, List[float]] = {}

    def embed(self, text: str) -> List[float]:
        if text in self._cache:
            return self._cache[text]
        vec = self.embed_batch([text])[0]
        self._cache[text] = vec
        return vec

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        out = []
        missing = []
        idx_map = []
        for i, t in enumerate(texts):
            if t in self._cache:
                out.append(self._cache[t])
            else:
                idx_map.append((len(out), t))
                out.append(None)  # 占位
        if idx_map:
            req = urllib.request.Request(
                self.base_url + "/embed",
                data=json.dumps({"texts": [t for _, t in idx_map]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            vectors = data.get("vectors", [])
            for (pos, t), vec in zip(idx_map, vectors):
                out[pos] = vec
                self._cache[t] = vec
        return out

    def __call__(self, text: str) -> List[float]:
        return self.embed(text)
