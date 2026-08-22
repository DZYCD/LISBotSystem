# -*- coding: utf-8 -*-
"""
LLM 客户端模块 - 从 bot.yaml 的 llm 配置启用，通过 JSON 激活工具调用和 TASK 创建。

系统提示词 = 角色设定 + 说话规则 + 上下文历史记忆（模板函数自动加载）+ 工具调用规则（TLL 统一 yaml）
"""

import os
import json
import yaml
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    """LLM 配置段，对应 bot.yaml 中的 llm 字段"""
    enabled: bool = False
    provider: str = 'openai'
    base_url: str = ''
    api_key_env: str = 'OPENAI_API_KEY'
    api_key: str = ''
    model: str = 'claude-opus-4-6'
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout: int = 60
    role_prompt: str = ''              # 角色设定
    style_prompt: str = ''             # 说话规则
    context_window: int = 200000
    compress_threshold: float = 0.8
    compress_min_turns: int = 20
    history_enabled: bool = True

    @staticmethod
    def from_dict(data: Dict) -> 'LLMConfig':
        if not data:
            return LLMConfig()
        return LLMConfig(
            enabled=data.get('enabled', False),
            provider=data.get('provider', 'openai'),
            base_url=data.get('base_url', ''),
            api_key_env=data.get('api_key_env', 'OPENAI_API_KEY'),
            api_key=data.get('api_key', ''),
            model=data.get('model', 'claude-opus-4-6'),
            temperature=data.get('temperature', 0.3),
            max_tokens=data.get('max_tokens', 4096),
            timeout=data.get('timeout', 60),
            role_prompt=data.get('role_prompt', ''),
            style_prompt=data.get('style_prompt', ''),
            context_window=data.get('context_window', 200000),
            compress_threshold=data.get('compress_threshold', 0.8),
            compress_min_turns=data.get('compress_min_turns', 20),
            history_enabled=data.get('history_enabled', True),
        )


class LLMClient:
    """LLM 统一客户端，直接面向任务创建与工具调用"""

    def __init__(self, config: LLMConfig, bot_context=None):
        self.config = config
        self._client = None
        self.bot_context = bot_context  # 关联 Bot，用于加载上下文和知识库
        self.tool_rules = self._load_tool_rules()
        if self.config.enabled:
            self._init_client()

    def _load_tool_rules(self) -> str:
        """从 TLL 的 tool_rules.yaml 加载工具调用规则（所有 AI 共用）"""
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tool_rules.yaml')
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            return data.get('tool_call_rules', '')
        except Exception as e:
            pass
            return ''

    def _init_client(self):
        try:
            from openai import OpenAI
            api_key = self.config.api_key or os.getenv(self.config.api_key_env, '')
            if not api_key:
                raise ValueError('LLM 未配置 API Key')
            self._client = OpenAI(
                base_url=self.config.base_url or 'https://api.openai.com/v1',
                api_key=api_key,
                timeout=self.config.timeout,
            )
        except Exception as e:
            pass
            self._client = None

    @property
    def is_ready(self) -> bool:
        return self._client is not None

    def chat(self, messages: List[Dict], response_format: Optional[Dict] = None) -> str:
        if not self.is_ready:
            raise RuntimeError('LLM 客户端未初始化')
        kwargs = {
            'model': self.config.model,
            'messages': messages,
            'temperature': self.config.temperature,
            'max_tokens': self.config.max_tokens,
        }
        if response_format:
            kwargs['response_format'] = response_format
        resp = self._client.chat.completions.create(**kwargs)
        # 打印 token 消耗（方便监控）
        try:
            usage = resp.usage
            bot_name = getattr(self.bot_context, 'config', None).name if self.bot_context and getattr(self.bot_context, 'config', None) else 'unknown'
            pass
        except Exception as e:
            pass
        return resp.choices[0].message.content or ''

    def _extract_json(self, text: str) -> Dict:
        """从 LLM 输出中提取 JSON，支持代码块包裹与前后杂质文本"""
        text = text.strip()
        print("输出结果：", text)
        # 剥离 Markdown 代码块标记
        if text.startswith('```'):
            lines = text.split('\n')
            if lines and lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            text = '\n'.join(lines)
        # 使用正则提取第一个 { 到最后一个 } 之间的内容
        import re
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            return {'reply': text, 'commands': []}
        try:
            return json.loads(match.group(0))
        except Exception:
            return {'reply': text, 'commands': []}

    def _build_registered_bots(self) -> Dict:
        """根据 bot_context.config.peers 动态构建可联系机器人列表（排除 SaYi_SV 节点）"""
        registered = {}
        if self.bot_context is None:
            return registered
        peers = getattr(self.bot_context.config, 'peers', {}) or {}
        for peer_id, peer_info in peers.items():
            if peer_id in ('agent/sayi_sv'):
                continue
            tools = peer_info.get('tools', []) if isinstance(peer_info, dict) else []
            registered[peer_id] = {'tools': tools}
        return registered

    def _build_system_prompt(self, user_input: str, history: List[Dict] = None) -> str:
        """四段式系统提示词：角色设定 + 说话规则 + 上下文历史记忆 + 工具调用规则"""
        sections = []

        if not hasattr(self, 'registered_bots'):
            self.registered_bots = self._build_registered_bots()

        # 1. 角色设定
        role = "# 角色设定\n" + self.config.role_prompt or '你是世忆图书馆 LIS 集群中的一名智能助手。'
        sections.append(role)

        # 2. 说话规则
        style = "# 说话规则\n" + self.config.style_prompt or '请根据任务要求简洁、准确、友好地回应。'
        sections.append(style)

        # 3. 上下文历史记忆（模板函数自动加载：未压缩上下文 + 知识库）
        if self.bot_context and hasattr(self.bot_context, 'build_context_prompt'):
            context = self.bot_context.build_context_prompt(user_input, recent_messages=history)
            if context:
                sections.append(context)

        # 4. 工具调用规则（来自 TLL 统一 yaml）
        if self.registered_bots:
            bot_desc_lines = []
            for bot_id, info in self.registered_bots.items():
                tools = info.get('tools', []) if isinstance(info, dict) else []
                tool_names = []
                for t in tools:
                    if isinstance(t, dict):
                        name = t.get('name', str(t))
                        params = t.get('params', {})
                        param_str = ''
                        if isinstance(params, dict) and params:
                            param_parts = []
                            for pname, pinfo in params.items():
                                if isinstance(pinfo, dict):
                                    ptype = pinfo.get('type', 'any')
                                    preq = '必填' if pinfo.get('required', False) else '可选'
                                    pdesc = pinfo.get('description', '')
                                else:
                                    ptype = 'any'
                                    preq = '可选'
                                    pdesc = ''
                                if pdesc:
                                    param_parts.append(f'{pname}({ptype},{preq}): {pdesc}')
                                else:
                                    param_parts.append(f'{pname}({ptype},{preq})')
                            param_str = '(' + ', '.join(param_parts) + ')'
                        tool_names.append(f'{name}{param_str}')
                    else:
                        tool_names.append(str(t))
                bot_desc_lines.append(f'- {bot_id}: 工具 ' + (', '.join(tool_names) if tool_names else '无'))
            bot_desc = '\n'.join(bot_desc_lines)
            sections.append(f'可用机器人列表：\n{bot_desc}')
        if self.tool_rules:
            sections.append(self.tool_rules)
        else:
            # 若未加载工具规则，使用内置默认回复规则
            sections.append("输出格式：{\"reply\": \"对话文本\", \"commands\": [{\"func_name\": \"工具名\", \"params\": {}}]}")

        return "\n\n".join(sections)

    def plan_task(self, user_input: str, history: List[Dict] = None) -> Dict[str, Any]:
        """
        根据用户输入生成 JSON 计划，支持 reply+commands 结构。
        返回格式：{"reply": "...", "commands": [{"target": "...", "command": "...", "params": {}}]}
        """
        if not self.is_ready:
            return {'reply': 'LLM 未启用。', 'commands': []}

        # LLM 调用前热加载最新配置（提示词实时生效）
        if self.bot_context is not None and hasattr(self.bot_context, 'reload'):
            self.bot_context.reload()
        self.registered_bots = self._build_registered_bots()

        system = self._build_system_prompt(user_input, history)
        messages = [{'role': 'system', 'content': system}]
        if history:
            for msg in history:
                messages.append({'role': msg.get('role', 'user'), 'content': msg.get('content', '')})
        messages.append({'role': 'user', 'content': user_input})

        print(messages)
        try:
            content = self.chat(
                messages,
            )
            plan = self._extract_json(content)
            if not isinstance(plan, dict):
                raise ValueError('LLM 输出不是 JSON 对象')
            # 规范化：确保包含 reply 和 commands
            plan.setdefault('reply', '已处理请求。')
            plan.setdefault('commands', [])
            if isinstance(plan['commands'], str):
                try:
                    plan['commands'] = json.loads(plan['commands'])
                except Exception:
                    plan['commands'] = []
            if not isinstance(plan['commands'], list):
                plan['commands'] = []
            return plan
        except Exception as e:
            return {'type': 'error', 'reason': f'LLM 调用失败: {e}', 'raw': content if 'content' in locals() else ''}

    def create_task_json(self, text: str, task_type: str = 'tool') -> Dict:
        return self.plan_task(text)

    def generate_knowledge_points(self, old_messages: List[Dict]) -> List[Dict]:
        """用于对话历史压缩时的知识点拆分"""
        if not self.is_ready:
            return []
        prompt = "将以下对话按主题拆分为知识点，输出JSON数组：[{\"topic\":\"\",\"summary\":\"\",\"keywords\":[]}]\n\n对话历史：\n"
        for msg in old_messages:
            prompt += f"[{msg.get('role')}] {msg.get('content', '')[:300]}\n"
        try:
            content = self.chat(
                [{'role': 'user', 'content': prompt}],
                response_format={'type': 'json_object'},
            )
            data = json.loads(content)
            if isinstance(data, dict):
                return data.get('knowledge_points', [])
            return data if isinstance(data, list) else []
        except Exception:
            return []


def create_llm_from_bot_config(config_obj, bot_context=None) -> Optional[LLMClient]:
    """从 BotConfig 的 llm 字段创建 LLMClient（若 enabled=false 则返回 None）"""
    llm_conf = getattr(config_obj, 'llm', None)
    if not llm_conf:
        return None
    config = LLMConfig.from_dict(llm_conf)
    if not config.enabled:
        return None
    return LLMClient(config, bot_context=bot_context)
