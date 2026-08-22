"""
Bot 基础数据结构 - LIS v2 TLL 协议
"""

import os
import json
import time
from datetime import datetime
import yaml
import importlib.util
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable

from .hook_manager import HookManager
from .core import TLLjson, create_task, create_logger, HIGHLIGHT, RESET, TaskStatus
from .history_manager import create_history_manager
from .llm import create_llm_from_bot_config, LLMClient, LLMConfig
from .knowledge_base import create_knowledge_base
from .dispatcher import AsyncLoggerDispatcher


@dataclass
class BotConfig:
    """机器人基础配置，对应 YAML 文件字段"""
    name: str = ''
    id: str = ''
    network: str = 'tcp'
    url: str = ''
    port: int = 0
    topic: str = ''
    auth_key: str = ''
    group: str = 'none'
    role: str = ''
    functions: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    scheduling: Dict[str, Any] = field(default_factory=lambda: {'mode': 'concurrent', 'max_concurrency': 4})
    network_healer: Dict[str, Any] = field(default_factory=lambda: {'type': 'tcp_healer', 'interval': 30})
    permissions: Dict[str, Any] = field(default_factory=lambda: {'accept_from': [], 'reject_from': []})
    fallback: Optional[str] = None
    networks: List[Dict[str, Any]] = field(default_factory=list)  # 多网络可选配置
    peers: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # 目标 bot 密钥映射
    llm: Dict[str, Any] = field(default_factory=dict)  # LLM 配置段（可选）
    harness: Dict[str, Any] = field(default_factory=dict)  # LIS-harness 配置段（可选）
    tool_list: str = ''  # 指向 harness 工具清单 yaml 的路径（相对 bot.yaml 所在目录）

    def get_network_options(self) -> List[Dict[str, Any]]:
        """获取网络配置列表。若未定义 networks，则根据顶层字段构造单选项。"""
        if self.networks:
            return self.networks
        return [{
            'network': self.network,
            'url': self.url,
            'port': self.port,
            'topic': self.topic,
        }]


class Bot:
    """运行时机器人实例"""

    def __init__(self, config: BotConfig, base_dir: str = None):
        self.config = config
        self.status = 'idle'
        self.task_queue = []
        self.active_tasks = []
        self.pending_queues = {}  # task_id -> 剩余命令队列（由发起者保存）
        self.queue_records = {}  # task_id -> 队列复核记录（每次复核后清空）
        self.registered = False
        self.handler_map = {}
        self.skills = {}
        self.base_dir = base_dir or '.'
        # 注册 TLL 统一 chat 工具（SaYi_SV 除外，主人不通过 chat 调用）
        if self.config.id != 'agent/sayi_sv':
            try:
                from .chat_tool import handle as chat_handle
                self.handler_map['chat'] = chat_handle
                chat_access = {}
                for t in self.config.tools:
                    if isinstance(t, dict) and t.get('name') == 'chat':
                        chat_access = t.get('access', {})
                        break
                self.skills['chat'] = {'name': 'chat', 'meta': {'description': '统一自然语言对话工具'}, 'path': '', 'access': chat_access, 'module': None}
            except Exception as e:
                pass
        self.hook_manager = HookManager(node_id=self.config.id)
        self.log_dispatcher = AsyncLoggerDispatcher(bot_id=self.config.id, publisher=self._publish_log_event)
        self.hook_manager.set_event_publisher(self.log_dispatcher.enqueue)
        self.log_dispatcher.start()
        self.sender = None
        self.outgoing_tasks = {}
        self._sv_registered = False
        # 新增：LLM 与历史管理器（通过一行 enable_llm 启用）
        self.llm_client = None
        self.history_manager = None
        self.knowledge_base = None
        self.enable_llm()

    def _publish_log_event(self, event):
        """将事件异步发送到监控中心（由独立线程调用，不阻塞主流程）"""
        try:
            import json
            import paho.mqtt.client as mqtt
            if not hasattr(self, '_log_mqtt') or self._log_mqtt is None:
                self._log_mqtt = mqtt.Client(client_id=f"log_{self.config.id.replace('/', '_')}_{id(self)}")
                self._log_mqtt.connect('broker.emqx.io', 1883, 60)
                self._log_mqtt.loop_start()
            if 'source_bot' not in event:
                event = dict(event)
                event['source_bot'] = self.config.id
            data = json.dumps(event, ensure_ascii=False, default=str).encode('utf-8')
            info = self._log_mqtt.publish('tll/skaye_SV', data, qos=2)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                self._log_mqtt.reconnect()
                self._log_mqtt.publish('tll/skaye_SV', data, qos=2)
        except Exception as e:
            pass

    def _init_history_from_llm(self):
        """根据 bot.yaml 的 llm.history_enabled 初始化对话历史管理器"""
        try:
            llm_conf = getattr(self.config, 'llm', None) or {}
            if not llm_conf.get('history_enabled', True):
                return
            hist_config = {
                'context': {
                    'window_tokens': llm_conf.get('context_window', 200000),
                    'compress_threshold': llm_conf.get('compress_threshold', 0.8),
                    'compress_min_turns': llm_conf.get('compress_min_turns', 20),
                },
                'history': {
                    'enabled': True,
                    'storage_dir': 'history',
                    'file_format': 'jsonl',
                    'summaries_dir': 'summaries',
                    'auto_compress': True,
                }
            }
            self.history_manager = create_history_manager(
                bot_id=self.config.id,
                base_dir=self.base_dir,
                config=hist_config
            )
            self.history_manager.llm_client = self.llm_client
        except Exception as e:
            pass

    def enable_llm(self):
        """根据 bot.yaml 的 llm 配置启用或禁用 LLM 功能（一行启用）"""
        llm_conf = getattr(self.config, 'llm', None) or {}
        if llm_conf.get('enabled', False):
            # 组装 LLMConfig
            llm_config_data = {
                'enabled': True,
                'provider': llm_conf.get('provider', 'openai'),
                'base_url': llm_conf.get('base_url', ''),
                'api_key_env': llm_conf.get('api_key_env', 'OPENAI_API_KEY'),
                'api_key': llm_conf.get('api_key', ''),
                'model': llm_conf.get('model', 'claude-opus-4-6'),
                'temperature': llm_conf.get('temperature', 0.3),
                'max_tokens': llm_conf.get('max_tokens', 4096),
                'timeout': llm_conf.get('timeout', 60),
                'role_prompt': llm_conf.get('role_prompt', ''),
                'style_prompt': llm_conf.get('style_prompt', ''),
                'context_window': llm_conf.get('context_window', 200000),
                'compress_threshold': llm_conf.get('compress_threshold', 0.8),
                'compress_min_turns': llm_conf.get('compress_min_turns', 20),
                'history_enabled': llm_conf.get('history_enabled', True),
            }
            self.llm_client = LLMClient(LLMConfig.from_dict(llm_config_data), bot_context=self)
            if self.llm_client.is_ready:
                pass
            else:
                pass
        else:
            self.llm_client = None
            pass
        # 历史管理器跟随 LLM 配置
        self._init_history_from_llm()
        # 知识库（始终可用）
        self.knowledge_base = create_knowledge_base(self.base_dir, self.config.id)

    def add_message(self, role: str, content: str, **metadata):
        """添加对话消息到历史记录（带 token 统计和自动压缩）"""
        if self.history_manager:
            return self.history_manager.add_message(role, content, **metadata)
        return None

    def add_tool_call(self, command: str, params: dict, result: Any, **metadata):
        """记录工具调用历史（用于追踪和画链接线）"""
        if self.history_manager:
            return self.history_manager.add_tool_call(command, params, result, **metadata)
        return None

    def search_knowledge(self, query: str, top_k: int = 5) -> list:
        """从知识库检索相关知识（供系统提示词模板调用）"""
        if self.knowledge_base:
            return self.knowledge_base.search(query, top_k=top_k)
        return []

    def get_knowledge_by_trigger(self, text: str) -> list:
        """根据触发词精确命中，返回匹配的完整知识条目（完整加载到上下文）"""
        if self.knowledge_base:
            return self.knowledge_base.search_by_keyword_hit(text)
        return []

    def build_context_prompt(self, user_input: str, recent_messages: list = None) -> str:
        """构建上下文历史记忆段，包含未压缩的最近对话和知识库检索结果"""
        sections = []
        history_text = ""
        # 未压缩对话
        if recent_messages:
            history_text = "\n".join(
                f"[{msg.get('role', 'user')}] {msg.get('content', '')}"
                for msg in recent_messages[-20:]
            )
        # 知识库触发词精确命中，完整加载知识条目
        if history_text + user_input:
            knowledge = self.get_knowledge_by_trigger(history_text + user_input)
            if knowledge:
                kb_lines = []
                for item in knowledge:
                    kb_lines.append("# 前情提要")
                    kb_lines.append(f"内容: {item.get('content', '')}")
                    kb_lines.append(f"触发词: {', '.join(item.get('keywords', []))}")
                sections.append("\n".join(kb_lines))
        return "\n".join(sections)

    def register(self):
        """注册机器人，并自动加载 skills 和自定义 hooks"""
        self.registered = True
        self.status = 'ready'
        self._load_hooks()
        self._load_skills()
        if 'ping' not in self.skills:
            def _ping_handler(params=None, **kwargs):
                return self.build_registration_info()
            self.handler_map['ping'] = _ping_handler
            self.skills['ping'] = {'name': 'ping', 'meta': {'description': '心跳检测，返回 PONG'}, 'path': '', 'access': {}, 'module': None}

    def reload(self):
        """热加载 bot.yaml，实时更新配置（不重置运行时状态）"""
        try:
            yaml_path = self.base_dir
            if os.path.isdir(yaml_path):
                for fname in ('bot.yaml', 'main.yaml', 'config.yaml'):
                    candidate = os.path.join(yaml_path, fname)
                    if os.path.isfile(candidate):
                        yaml_path = candidate
                        break
                else:
                    return False
            elif not os.path.isfile(yaml_path):
                return False
            with open(yaml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            self.config.peers = data.get('peers', self.config.peers)
            self.config.llm = data.get('llm', self.config.llm)
            self.config.tools = data.get('tools', self.config.tools)
            self.config.permissions = data.get('permissions', self.config.permissions)
            self.config.fallback = data.get('fallback', self.config.fallback)
            self.config.auth_key = data.get('auth_key', self.config.auth_key)
            self.config.networks = data.get('networks', self.config.networks)
            if self.sender is not None:
                self.sender.peers = self.config.peers
            if self.llm_client is not None and data.get('llm'):
                llm_conf = data['llm']
                llm_cfg = self.llm_client.config
                llm_cfg.role_prompt = llm_conf.get('role_prompt', llm_cfg.role_prompt)
                llm_cfg.style_prompt = llm_conf.get('style_prompt', llm_cfg.style_prompt)
                llm_cfg.model = llm_conf.get('model', llm_cfg.model)
                llm_cfg.temperature = llm_conf.get('temperature', llm_cfg.temperature)
                llm_cfg.max_tokens = llm_conf.get('max_tokens', llm_cfg.max_tokens)
                llm_cfg.base_url = llm_conf.get('base_url', llm_cfg.base_url)
                llm_cfg.api_key = llm_conf.get('api_key', llm_cfg.api_key)
            self._load_skills()
            return True
        except Exception as e:
            print(f"[热加载] 刷新配置失败: {e}")
            return False

    def _load_hooks(self):
        """从 base_dir/hooks.py 加载自定义 hook，未定义的级别使用默认实现"""
        # 延迟导入，避免循环依赖
        from .hooks import (
            error_hook, warning_hook, success_hook, info_hook, debug_hook, finish_hook
        )

        default_hooks = {
            'error': error_hook,
            'warning': warning_hook,
            'success': success_hook,
            'info': info_hook,
            'debug': debug_hook,
            'finish': finish_hook
        }

        # 先注册默认 hooks，再尝试加载自定义覆盖
        for level, hook in default_hooks.items():
            self.hook_manager.register_hook(level, hook)

        hooks_path = os.path.join(self.base_dir, 'hooks.py')
        if not os.path.isfile(hooks_path):
            return

        spec = importlib.util.spec_from_file_location(f"{self.config.name}_hooks", hooks_path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            pass
            return

        for level in list(default_hooks.keys()):
            func = getattr(module, level, None)
            if callable(func):
                self.hook_manager.register_hook(level, func)

    def _load_skills(self):
        """扫描 skills/ 目录，加载每个工具到 handler_map"""
        skills_root = os.path.join(self.base_dir, 'skills')
        if not os.path.isdir(skills_root):
            return

        # 解析工具访问控制配置（从 bot.yaml 的 tools 字段）
        tool_access_map = {}
        for item in self.config.tools:
            if isinstance(item, str):
                tool_access_map[item] = {}
            elif isinstance(item, dict):
                name = item.get('name')
                if name:
                    tool_access_map[name] = item.get('access', {})

        for skill_name in os.listdir(skills_root):
            skill_dir = os.path.join(skills_root, skill_name)
            if not os.path.isdir(skill_dir):
                continue

            yaml_path = os.path.join(skill_dir, 'tool.yaml')
            if not os.path.isfile(yaml_path):
                continue
            try:
                with open(yaml_path, 'r', encoding='utf-8') as f:
                    meta = yaml.safe_load(f) or {}
            except Exception:
                continue

            py_path = os.path.join(skill_dir, 'tool.py')
            if not os.path.isfile(py_path):
                continue

            spec = importlib.util.spec_from_file_location(f"skill_{skill_name}", py_path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:
                continue

            handler = getattr(module, 'handle', None)
            if handler is None:
                continue

            self.handler_map[skill_name] = handler
            self.skills[skill_name] = {'name': skill_name, 'meta': meta, 'path': skill_dir, 'access': tool_access_map.get(skill_name, {}), 'module': module}

    def build_registration_info(self):
        """构建注册/心跳上报信息，record_lis 与 ping 返回统一内容。"""
        avatar_files = []
        static_dir = os.path.join(self.base_dir, 'static')
        if os.path.isdir(static_dir):
            for f in os.listdir(static_dir):
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    avatar_files.append(f)
        skills_info = {}
        for name, skill in self.skills.items():
            meta = skill.get('meta', {}) or {}
            skills_info[name] = {
                'name': name,
                'description': meta.get('description', ''),
                'access': skill.get('access', {})
            }
        related_map = {
            'agent/sayi_996': ['agent/sky_001', 'agent/sky_002'],
            'agent/sky_001': ['agent/sayi_996'],
        }
        # 提取 peer：只保留 sayi_XXX ↔ skaye_XXX 对称配对，SV 监管不参与
        peer = None
        my_id = self.config.id
        my_parts = my_id.split('/')[-1].split('_') if '/' in my_id else my_id.split('_')
        prefix = my_parts[0].lower() if my_parts else ''
        suffix = my_parts[1] if len(my_parts) > 1 else None
        is_sv = suffix and suffix.lower() == 'sv'
        if not is_sv and prefix in ('sayi', 'skaye') and suffix:
            target_prefix = 'skaye' if prefix == 'sayi' else 'sayi'
            exact = f'agent/{target_prefix}_{suffix}'
            if self.config.peers and exact in self.config.peers:
                peer = exact
        # 提取 MQTT 网络信息（取 networks 中第一个 mqtt 配置）
        network_info = {}
        for net in getattr(self.config, 'networks', []) or []:
            if net.get('network') == 'mqtt':
                network_info = {'url': net.get('url', ''), 'port': net.get('port', ''), 'topic': net.get('topic', '')}
                break
        return {
            'bot_id': self.config.id,
            'group': self.config.group,
            'name': self.config.name,
            'tools': list(self.skills.keys()),
            'skills': skills_info,
            'avatars': avatar_files,
            'related_bots': related_map.get(self.config.id, []),
            'peer': peer,
            'auth_key': self.config.auth_key,
            'network': network_info
        }

    def set_sender(self, sender):
        """注入发送器，用于发起任务"""
        self.sender = sender
        if self.sender is not None and not getattr(self, '_sv_registered', False):
            self._sv_registered = True
            if self.config.id not in ('agent/skaye_sv', 'agent/sayi_sv'):
                if 'agent/skaye_sv' not in self.config.peers:
                    self.config.peers['agent/skaye_sv'] = {'auth_key': 'sk-sv'}
                if 'agent/sayi_sv' not in self.config.peers:
                    self.config.peers['agent/sayi_sv'] = {'auth_key': 'sk-sayi-sv'}
                accept_from = self.config.permissions.get('accept_from', [])
                if 'agent/sayi_sv' not in accept_from:
                    accept_from.append('agent/sayi_sv')
                    self.config.permissions['accept_from'] = accept_from
                info = self.build_registration_info()
                _t = self.create_task('agent/skaye_sv', 'record_lis', info)
                self.send_command(_t)

    def create_task(self, target: str, command: str = None, params: dict = None, commands: list = None, on_archive=None) -> Any:
        """
        创建委托任务（仅创建，不发送）。
        支持单个命令或命令列表（commands 参数）。
        返回 task 对象。
        """
        if commands is not None:
            if not isinstance(commands, list) or len(commands) == 0:
                raise ValueError('commands must be a non-empty list')
            first = commands[0]
            if isinstance(first, dict):
                target = first.get('target', target)
                command = first.get('command') or first.get('func_name') or ''
                params = first.get('params', {}) or {}
            else:
                command = first
        tlljson = TLLjson(
            from_bot=self.config.id,
            command=command,
            to=target,
            params=params or {}
        )
        task = create_task(
            task_type='tool',
            from_bot=self.config.id,
            current_agent=self.config.id,
            tlljson=tlljson
        )
        if commands is not None and len(commands) > 1:
            pending = []
            for cmd in commands[1:]:
                if isinstance(cmd, dict):
                    cmd_target = cmd.get('target', target)
                    cmd_command = cmd.get('command') or cmd.get('func_name') or ''
                    cmd_params = cmd.get('params', {}) or {}
                else:
                    cmd_target = target
                    cmd_command = cmd
                    cmd_params = {}
                if cmd_target and cmd_command:
                    pending.append({'target': cmd_target, 'command': cmd_command, 'params': cmd_params})
            if pending:
                self.pending_queues[task.id] = pending
        # 通过 TASK logger 记录调试信息
        logger = create_logger(task, task_type='tool', hook_manager=self.hook_manager)
        debug_info = {
            'action': 'task_created',
            'task_id': task.id,
            'type': task.type,
            'status': task.status.value,
            'from_bot': task.from_bot,
            'current_agent': task.current_agent,
            'tlljson': {
                'from_bot': tlljson.from_bot,
                'command': tlljson.command,
                'to': tlljson.to,
                'params': tlljson.params,
            },
            'trace': task.trace.to_dict()
        }
        logger.debug(json.dumps(debug_info, ensure_ascii=False, indent=2))
        task._set_status(TaskStatus.CREATED)
        if on_archive is not None:
            if not hasattr(self, '_archive_callbacks'):
                self._archive_callbacks = {}
            self._archive_callbacks[task.id] = on_archive
        return task

    def send_command(self, task, target: str = None) -> str:
        """
        发送一个已创建的 TASK（不创建新任务）。
        返回 task_id。
        """
        # 发送前热加载最新配置（peers/auth_key/llm 提示词实时生效）
        self.reload()
        if task is None:
            raise ValueError("task is required")
        target = target or (task.tlljson.to if task.tlljson else '')
        if not target:
            raise ValueError("target is required")
        command = task.tlljson.command if task.tlljson else ''
        params = task.tlljson.params if task.tlljson else {}

        # 每次向前委托（非 reply/error 回传）时 delegate_count+1
        if command not in ('reply', 'error'):
            task.delegate_count = getattr(task, 'delegate_count', 0) + 1

        # 通过 TASK logger 记录发送任务的调试信息（含委托具体字段）
        logger = getattr(task, 'logger', None)
        if logger is None:
            logger = create_logger(task, task_type='tool', hook_manager=self.hook_manager)
        debug_info = {
            'action': 'task_sent',
            'task_id': task.id,
            'type': getattr(task.type, 'value', str(task.type)),
            'status': getattr(task.status, 'value', str(task.status)),
            'from_bot': task.from_bot,
            'current_agent': task.current_agent,
            'target': target,
            'command': command,
            'params': params,
            'trace': task.trace.to_dict() if hasattr(task.trace, 'to_dict') else str(task.trace)
        }
        logger.debug(json.dumps(debug_info, ensure_ascii=False, indent=2))

        self.outgoing_tasks[task.id] = {
            'sent_at': time.time(),
            'task': task,
            'status': task.status,
            'completed_at': None,
            'target': target,
            'command': command,
            'params': params,
            'result': None,
            'output': None
        }
        if self.sender is None:
            task.logger.error(f"未设置 sender，无法发送任务 {task.id}")
            return task.id
        self.sender.send_task(task, target)
        task.logger.info(f"已发送任务到 {HIGHLIGHT}{target}{RESET}，命令 {HIGHLIGHT}{command}{RESET}")
        return task.id

    def complete_task(self, task_id: str, status, result=None, output=None):
        """更新本地任务追踪状态，并保存结果，计算耗时。任务完成后从 outgoing_tasks 移除，避免内存泄漏。"""
        if task_id not in self.outgoing_tasks:
            return
        record = self.outgoing_tasks[task_id]
        record['status'] = status
        record['completed_at'] = time.time()
        if result is not None:
            record['result'] = result
        if output is not None:
            record['output'] = output
        if record['sent_at'] is not None:
            record['duration'] = record['completed_at'] - record['sent_at']
        task_obj = record.get('task')
        if task_obj is not None and getattr(task_obj, 'logger', None):
            task_obj.logger.info(f"任务完成，状态={status}，耗时={record.get('duration', 'N/A')}s")
        else:
            pass



    def get_task_tracker(self) -> Dict[str, Any]:
        """返回当前任务追踪信息"""
        return self.outgoing_tasks

    def to_dict(self) -> Dict[str, Any]:
        return {
            'config': {
                'name': self.config.name,
                'id': self.config.id,
                'network': self.config.network,
                'url': self.config.url,
                'port': self.config.port,
                'topic': self.config.topic,
                'group': self.config.group,
                'role': self.config.role,
                'functions': self.config.functions,
                'tools': self.config.tools,
                'scheduling': self.config.scheduling,
                'network_healer': self.config.network_healer,
                'permissions': self.config.permissions,
                'fallback': self.config.fallback,
                'networks': self.config.networks,
                'peers': self.config.peers
            },
            'status': self.status,
            'active_tasks': len(self.active_tasks),
            'skills': list(self.skills.keys()),
            'hooks': list(self.hook_manager._hooks.keys()),
            'outgoing_tasks': self.outgoing_tasks
        }
