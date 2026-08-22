# -*- coding: utf-8 -*-
"""
AI 计划器 - 读取 ai_config.yaml，调用 LLM 生成工具调用计划。

计划输出格式严格为 JSON：
{
  "target": "agent/eiar_001",
  "command": "file_read",
  "params": {"path": "..."},
  "reason": "简要说明"
}
"""

import os
import json
import yaml


class AIPlanner:
    """从配置读取模型，根据任务与注册信息生成工具调用计划"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ai_config.yaml')
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f) or {}
        self.ai_cfg = self.config.get('ai', {})
        self.context_cfg = self.config.get('context', {})
        self.history_cfg = self.config.get('history', {})
        self._client = None
        self._init_client()

    def _init_client(self):
        """初始化 OpenAI SDK 客户端（延迟加载）"""
        try:
            from openai import OpenAI
            api_key = self.ai_cfg.get('api_key') or os.getenv(self.ai_cfg.get('api_key_env', 'OPENAI_API_KEY'), '')
            if not api_key:
                raise ValueError("缺少 API Key")
            self._client = OpenAI(
                base_url=self.ai_cfg.get('base_url'),
                api_key=api_key,
                timeout=self.ai_cfg.get('timeout', 60)
            )
        except Exception as e:
            print(f"[AIPlanner] 初始化 LLM 客户端失败: {e}")
            self._client = None

    def _build_system_prompt(self, registered_bots: dict = None) -> str:
        """从配置读取系统提示词，并附加机器人清单"""
        sys_prompt = self.ai_cfg.get('system_prompt', '')
        if registered_bots:
            bot_list = []
            for bot_id, info in registered_bots.items():
                tools = info.get('tools', [])
                bot_list.append(f"- {bot_id} ({info.get('name', '')}) 工具: {', '.join(tools) if tools else '无'}")
            sys_prompt += "\n\n当前可用机器人：\n" + "\n".join(bot_list)
        return sys_prompt

    def generate_plan(self, task_text: str, registered_bots: dict = None, history_messages: list = None) -> dict:
        """
        将自然语言任务转换为工具调用计划。
        返回 dict，失败时返回 {'error': 原因}。
        """
        if self._client is None:
            return {'error': 'LLM 客户端未初始化'}
        try:
            messages = [{'role': 'system', 'content': self._build_system_prompt(registered_bots)}]
            if history_messages:
                # 仅取最近的部分对话作为上下文
                for msg in history_messages:
                    messages.append({'role': msg.get('role', 'user'), 'content': msg.get('content', '')})
            messages.append({'role': 'user', 'content': task_text})

            response = self._client.chat.completions.create(
                model=self.ai_cfg.get('model', 'claude-opus-4-6'),
                messages=messages,
                temperature=self.ai_cfg.get('temperature', 0.3),
                max_tokens=self.ai_cfg.get('max_tokens', 4096),
                response_format={'type': 'json_object'},
            )
            content = response.choices[0].message.content or ''
            # 清理可能的 markdown 代码块
            if content.startswith('```'):
                content = content.strip('`')
                if content.startswith('json'):
                    content = content[4:]
            plan = json.loads(content)
            if not isinstance(plan, dict):
                raise ValueError('LLM 输出不是 JSON 对象')
            if 'command' not in plan:
                raise ValueError('缺少 command 字段')
            return plan
        except json.JSONDecodeError as e:
            return {'error': f'LLM 输出 JSON 解析失败: {e}', 'raw': content if 'content' in locals() else ''}
        except Exception as e:
            return {'error': f'调用 LLM 失败: {e}'}

    def compress_knowledge_points(self, old_messages: list) -> list:
        """供 history_manager 调用的智能压缩入口"""
        if self._client is None:
            return []
        prompt = "请将以下对话历史按领域拆分为多个知识点，每个知识点包含 topic、summary（200字内）和 keywords。\n要求：1.识别不同主题 2.每个主题生成一个知识点 3.以 JSON 数组格式输出：[{\"topic\":\"\",\"summary\":\"\",\"keywords\":[]}]\n\n对话历史：\n"
        for msg in old_messages:
            prompt += f"[{msg.get('role')}] {msg.get('content', '')}\n"
        try:
            response = self._client.chat.completions.create(
                model=self.ai_cfg.get('model'),
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.2,
                max_tokens=2000,
                response_format={'type': 'json_object'},
            )
            content = response.choices[0].message.content or '[]'
            data = json.loads(content)
            if isinstance(data, dict):
                return data.get('knowledge_points', [])
            return data if isinstance(data, list) else []
        except Exception:
            return []


# 全局单例
def get_planner(config_path: str = None) -> AIPlanner:
    return AIPlanner(config_path)
