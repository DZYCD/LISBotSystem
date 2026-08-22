"""
对话历史统一管理器 - TLL Protocol 模板

所有 LIS 机器人的对话历史保存/压缩/知识点拆分均使用此模块。

设计要点：
1. 按机器人独立存储：bots/{bot_id}/history/YYYYMMDD_HHMMSS_<session_id>.jsonl
2. Token 估算（保守）：1个汉字≈2 token，1个英文单词≈0.5 token
3. 达到上下文窗口80%时触发自动压缩，压缩后保留最近 compress_min_turns 轮
4. 压缩时调用 LLM 将旧对话按领域拆分为多个知识点，摘要独立保存至 summaries/
5. 支持将知识点注入 worldsmemory（或通过 MEMORY_ADD 命令）
"""

import os
import json
import time
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any


def estimate_tokens(text: str) -> int:
    """保守估算 token 数：1汉字≈2token，1英文≈0.5token"""
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 2 + other_chars * 0.5)


def estimate_message_tokens(message: Dict) -> int:
    """估算单条消息的 token 数"""
    total = estimate_tokens(message.get('content', ''))
    total += estimate_tokens(message.get('role', ''))
    for tool_call in message.get('tool_calls', []):
        total += estimate_tokens(json.dumps(tool_call))
    total += estimate_tokens(json.dumps(message.get('tool_result', {})))
    return total


def generate_session_id() -> str:
    """生成会话 ID（基于时间戳+随机哈希）"""
    return hashlib.md5(f'{time.time()}-{os.getpid()}'.encode()).hexdigest()[:8]


class HistoryManager:
    """对话历史管理器"""

    def __init__(self, bot_id: str, base_dir: str, config: Dict):
        self.bot_id = bot_id
        self.base_dir = base_dir
        self.context = config.get('context', {})
        self.history = config.get('history', {})
        self.window_tokens = self.context.get('window_tokens', 200000)
        self.threshold = self.context.get('compress_threshold', 0.8)
        self.min_turns = self.context.get('compress_min_turns', 20)
        self.session_id = generate_session_id()
        self.storage_dir = self._resolve_storage_dir()
        self.summaries_dir = os.path.join(self.storage_dir, self.history.get('summaries_dir', 'summaries'))
        os.makedirs(self.storage_dir, exist_ok=True)
        os.makedirs(self.summaries_dir, exist_ok=True)
        # 统一存储到单一大文件 history.jsonl
        self.current_file = os.path.join(self.storage_dir, 'history.jsonl')
        self._messages: List[Dict] = []
        self._total_tokens: int = 0
        self.llm_client = None  # 绑定机器人的 LLM 客户端，可选
        self._load_existing()

    def _resolve_storage_dir(self) -> str:
        """解析存储目录（支持 {bot_id} 占位符）"""
        storage_conf = self.history.get('storage_dir', 'history')
        path = storage_conf.replace('{bot_id}', self.bot_id)
        if not os.path.isabs(path):
            path = os.path.join(self.base_dir, path)
        return path

    def _load_existing(self):
        # 从所有历史文件重新计算 token 数（不缓存消息到内存）
        self._total_tokens = 0
        if not os.path.isdir(self.storage_dir):
            return
        try:
            if os.path.isfile(self.current_file):
                with open(self.current_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                            if msg.get('type') == 'compression_marker':
                                continue
                            self._total_tokens += msg.get('tokens', 0)
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        # 保持 _messages 为空，不再缓存消息
        self._messages = []

    def _read_all_messages(self) -> List[Dict]:
        # 从所有历史 jsonl 文件读取消息（按文件名排序，过滤压缩标记）
        messages = []
        if not os.path.isdir(self.storage_dir):
            return messages
        try:
            if os.path.isfile(self.current_file):
                with open(self.current_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line)
                            if msg.get('type') == 'compression_marker':
                                continue
                            messages.append(msg)
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        return messages

    def _create_new_file(self) -> str:
        """创建新的历史文件（按时间戳命名）"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return os.path.join(self.storage_dir, f'{timestamp}_{self.session_id}.jsonl')

    def add_message(self, role: str, content: str, **metadata) -> Dict:
        """添加一条消息，自动估算 token，检查是否需要压缩"""
        msg = {
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat(),
            'tokens': estimate_tokens(content),
            'session_id': self.session_id,
        }
        msg.update(metadata)
        self._total_tokens += msg['tokens']
        self._append_to_file(msg)
        if self.history.get('auto_compress', True):
            self.check_compress()
        return msg

    def add_tool_call(self, command: str, params: Dict, result: Any, **metadata) -> Dict:
        """工具调用记录已废弃，不再写入历史。"""
        return None

    def _append_to_file(self, msg: Dict):
        """追加写入 JSONL 文件"""
        try:
            with open(self.current_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(msg, ensure_ascii=False, default=str) + '\n')
        except Exception as e:
            pass

    def get_messages(self, limit: int = None) -> List[Dict]:
        # 获取所有历史消息（从文件读取，不依赖内存缓存）
        messages = self._read_all_messages()
        if limit:
            return messages[-limit:]
        return messages

    def get_total_tokens(self) -> int:
        return self._total_tokens

    def check_compress(self):
        """检查是否达到压缩阈值"""
        threshold_tokens = self.window_tokens * self.threshold
        if self._total_tokens >= threshold_tokens:
            self.compress()

    def compress(self, llm_client=None):
        """触发压缩：提取旧对话生成摘要，保留最近 min_turns 轮"""
        pass
        messages = self._read_all_messages()
        if len(messages) < 2:
            return

        # 分离旧消息（压缩前50%，保留后50%在源文件中）
        split_index = len(messages) // 2
        old_messages = messages[:split_index]
        new_messages = messages[split_index:]

        # 调用 LLM 生成领域知识点（若提供客户端）
        knowledge_points = self._generate_knowledge_points(old_messages, llm_client)

        # 保存摘要文件
        summary = {
            'timestamp': datetime.now().isoformat(),
            'bot_id': self.bot_id,
            'session_id': self.session_id,
            'old_message_count': len(old_messages),
            'knowledge_points': knowledge_points,
            'context_before': {
                'total_tokens': self._total_tokens,
                'message_count': len(messages),
            },
        }
        summary_file = os.path.join(self.summaries_dir, f'{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        # 将保留的50%消息写回源文件（覆盖），不创建新文件
        with open(self.current_file, 'w', encoding='utf-8') as f:
            for m in new_messages:
                f.write(json.dumps(m, ensure_ascii=False, default=str) + '\n')
        self._total_tokens = sum(estimate_message_tokens(m) for m in new_messages)

        # 自动将知识点保存到长期记忆
        self._save_knowledge_points_to_memory(knowledge_points)

        # 自动将知识点保存到知识库（knowledge_base.json）
        self._save_knowledge_points_to_kb(knowledge_points)

        # 写入压缩标记到当前文件
        marker = {
            'type': 'compression_marker',
            'timestamp': datetime.now().isoformat(),
            'summary_file': os.path.basename(summary_file),
            'knowledge_points': knowledge_points,
            'remaining_turns': len(new_messages),
        }
        self._append_to_file(marker)
        pass
        return knowledge_points

    def _generate_knowledge_points(self, old_messages: List[Dict], llm_client=None):
        """使用 LLM 将旧对话拆分为多个领域知识点；若无 LLM 则简单汇总"""
        # 若未显式传入且实例绑定了 llm_client，则使用绑定客户端
        if llm_client is None and getattr(self, 'llm_client', None) is not None:
            llm_client = self.llm_client
        if llm_client is None:
            # 无 LLM 时的降级处理：将每条旧消息作为简单知识点
            points = []
            for msg in old_messages:
                content = msg.get('content', '')
                if content:
                    points.append({
                        'topic': '通用',
                        'summary': content[:200],
                        'keywords': [msg.get('role', '')],
                        'source': 'auto_summary',
                    })
            return points

        # 使用 LLMClient 的 generate_knowledge_points 方法（自动从 bot.yaml 读取配置）
        try:
            points = llm_client.generate_knowledge_points(old_messages)
            return points
        except Exception as e:
            pass
            return self._generate_knowledge_points(old_messages, None)

    def _build_compress_prompt(self, messages: List[Dict]) -> str:
        """构造压缩提示词，要求模型输出多个知识点"""
        lines = ["请将以下对话历史按领域拆分为多个知识点，每个知识点包含 topic、summary（200字内）和 keywords。",
                 "要求：",
                 "1. 识别对话中涉及的不同主题（如编程、网络、文档处理、任务调度等）",
                 "2. 每个主题生成一个知识点，summary 需包含关键事实和决策",
                 "3. 以 JSON 格式输出：{\"knowledge_points\": [{\"topic\": \"\", \"summary\": \"\", \"keywords\": []}]}",
                 "\n对话历史："]
        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content', '')[:500]
            lines.append(f"[{role}] {content}")
        return '\n'.join(lines)

    def export_knowledge_points(self):
        """导出所有摘要知识点（供写入长期记忆）"""
        points = []
        if not os.path.isdir(self.summaries_dir):
            return points
        for fname in os.listdir(self.summaries_dir):
            if not fname.endswith('.json'):
                continue
            try:
                with open(os.path.join(self.summaries_dir, fname), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                points.extend(data.get('knowledge_points', []))
            except Exception:
                continue
        return points

    def save_knowledge_points(self, points: List[Dict]):
        """将知识点批量写入长期记忆（通过 MEMORY_ADD 指令）"""
        # 实际需要转发给 MEMORY_ADD 命令，这里提供接口
        return points

    def get_recent_turns(self, turns: int = 20) -> List[Dict]:
        # 获取最近 N 轮对话（从文件读取）
        messages = self._read_all_messages()
        return messages[-turns:]

    def _save_knowledge_points_to_kb(self, points: List[Dict]):
        """将压缩得到的知识点保存到知识库（knowledge_base.json）"""
        if not points:
            return
        try:
            from .knowledge_base import KnowledgeBase
            kb = KnowledgeBase(self.base_dir, self.bot_id)
            for point in points:
                kb.add(
                    content=point.get('summary', ''),
                    keywords=point.get('keywords', []),
                    updates=[{'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'action': 'create', 'note': '上下文压缩自动写入'}]
                )
            pass
        except Exception as e:
            pass

    def _save_knowledge_points_to_memory(self, points: List[Dict]):
        """将压缩生成的知识点保存到该机器人的 worldsmemory.json 中"""
        if not points:
            return
        mem_path = os.path.join(self.base_dir, 'worldsmemory.json')
        memory = []
        if os.path.exists(mem_path):
            try:
                with open(mem_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        memory = data
            except Exception:
                pass
        for point in points:
            memory.append({
                "知识点": point.get('summary', ''),
                "触发词": point.get('keywords', []),
                "来源": f"对话历史压缩 - {self.bot_id}",
                "时间": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
        try:
            with open(mem_path, 'w', encoding='utf-8') as f:
                json.dump(memory, f, ensure_ascii=False, indent=2)
            pass
        except Exception as e:
            pass


# 全局工厂
def create_history_manager(bot_id: str, base_dir: str, config: Dict) -> HistoryManager:
    return HistoryManager(bot_id, base_dir, config)
