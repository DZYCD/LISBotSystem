# -*- coding: utf-8 -*-
"""
知识库统一管理模块 - 用于保存上下文压缩后的知识点。

每条知识包含：
- id: 唯一标识
- updates: 更新日志（列表，每次更新追加记录）
- content: 详细内容
- keywords: 触发词列表

存储格式为 JSON，默认路径为 {bot_base_dir}/knowledge_base.json。
"""

import os
import json
import uuid
import time
from typing import List, Dict, Optional, Any


class KnowledgeItem:
    """知识条"""
    def __init__(self, id: str = None, content: str = '', keywords: List[str] = None, updates: List[Dict] = None):
        self.id = id or uuid.uuid4().hex[:8]
        self.content = content
        self.keywords = keywords or []
        self.updates = updates or []

    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'content': self.content,
            'keywords': self.keywords,
            'updates': self.updates,
        }

    @staticmethod
    def from_dict(data: Dict) -> 'KnowledgeItem':
        return KnowledgeItem(
            id=data.get('id'),
            content=data.get('content', ''),
            keywords=data.get('keywords', []),
            updates=data.get('updates', []),
        )


class KnowledgeBase:
    """知识库管理器"""

    def __init__(self, base_dir: str = None, bot_id: str = ''):
        self.base_dir = base_dir or '.'
        self.bot_id = bot_id
        self.file_path = os.path.join(self.base_dir, 'knowledge_base.json')
        self.items: Dict[str, KnowledgeItem] = {}
        self.load()

    def load(self):
        """从文件加载知识库"""
        if not os.path.exists(self.file_path):
            return
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                for item_data in data:
                    item = KnowledgeItem.from_dict(item_data)
                    self.items[item.id] = item
            elif isinstance(data, dict):
                for item_data in data.values():
                    item = KnowledgeItem.from_dict(item_data)
                    self.items[item.id] = item
        except Exception as e:
            pass

    def save(self):
        """保存知识库到 JSON 文件"""
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump([item.to_dict() for item in self.items.values()], f, ensure_ascii=False, indent=2)
        except Exception as e:
            pass

    def add(self, content: str, keywords: List[str], updates: List[Dict] = None, id: str = None) -> KnowledgeItem:
        """新增知识，返回创建的知识条"""
        item = KnowledgeItem(id=id, content=content, keywords=keywords, updates=updates or [])
        if not item.updates:
            item.updates.append({
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'action': 'create',
                'note': '初始创建'
            })
        self.items[item.id] = item
        self.save()
        return item

    def update(self, id: str, content: str = None, keywords: List[str] = None, note: str = '') -> bool:
        """更新知识条，追加更新日志"""
        if id not in self.items:
            return False
        item = self.items[id]
        if content is not None:
            item.content = content
        if keywords is not None:
            item.keywords = keywords
        item.updates.append({
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'action': 'update',
            'note': note or '内容更新',
        })
        self.save()
        return True

    def delete(self, id: str) -> bool:
        """删除知识条"""
        if id in self.items:
            del self.items[id]
            self.save()
            return True
        return False

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """根据触发词或内容搜索知识，返回最匹配的条目"""
        results = []
        query_lower = query.lower()
        for item in self.items.values():
            score = 0
            # 触发词匹配
            for kw in item.keywords:
                if kw.lower() in query_lower:
                    score += 3
            # 内容匹配
            if item.content and query_lower in item.content.lower():
                score += 1
            if score > 0:
                results.append({'item': item.to_dict(), 'score': score})
        results.sort(key=lambda x: x['score'], reverse=True)
        return [r['item'] for r in results[:top_k]]

    def search_by_keyword_hit(self, query: str) -> List[Dict]:
        """精确触发词命中：只要上下文包含知识条中的任一触发词，就返回该完整知识条目"""
        matched = []
        query_lower = query.lower()
        for item in self.items.values():
            if any(kw and kw.lower() in query_lower for kw in item.keywords):
                matched.append(item.to_dict())
        return matched

    def get_all(self) -> List[Dict]:
        """获取所有知识条"""
        return [item.to_dict() for item in self.items.values()]

    def add_from_summary(self, summary: Dict, source_bot: str = '') -> List[KnowledgeItem]:
        """从压缩摘要中提取知识点并添加到知识库"""
        created = []
        for point in summary.get('knowledge_points', []):
            content = point.get('summary', '')
            if not content:
                continue
            keywords = point.get('keywords', [])
            topic = point.get('topic', '通用')
            note = summary.get('timestamp', '')
            item = self.add(
                content=content,
                keywords=keywords,
                updates=[{'timestamp': note, 'action': 'create', 'note': f'上下文压缩 - {source_bot}'}]
            )
            created.append(item)
        return created


def create_knowledge_base(base_dir: str, bot_id: str) -> KnowledgeBase:
    """工厂函数"""
    return KnowledgeBase(base_dir, bot_id)
