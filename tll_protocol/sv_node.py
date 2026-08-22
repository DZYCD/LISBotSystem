"""SV 节点公共基类 - 供 Skaye_SV / SaYi_SV 等特殊节点复用。
将 transport 创建、消息接收、debug 记录、强制归档等复杂逻辑
统一封装在 TLL 层，使 bots 目录下的 start.py 保持简洁。
"""

import os
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .bot_factory import request_bot_create
from .mqtt_transport import MQTTTransport
from .receiver import TaskReceiver
from .executor import TaskExecutor
from .task_sender import TaskSender
from .core import create_logger


class SVNodeBase:
    """SV 节点基类"""

    def __init__(self, bot_dir, additional_topics=None, node_name=''):
        self.node_name = node_name
        self.additional_topics = additional_topics or []
        self.bot = request_bot_create(bot_dir)
        self.bot.hook_manager.node_id = self.bot.config.id

        self.transport = self._create_transport(self.additional_topics)
        self.sender = TaskSender(
            transport=self.transport,
            bot_id=self.bot.config.id,
            peers=self.bot.config.peers,
            group=self.bot.config.group
        )
        self.bot.set_sender(self.sender)
        self.receiver = TaskReceiver(
            bot_id=self.bot.config.id,
            auth_key=self.bot.config.auth_key
        )
        self.executor = TaskExecutor(
            handler_map=self.bot.handler_map,
            sender=self.sender,
            bot_context=self.bot
        )
        self._thread_pool = ThreadPoolExecutor(max_workers=4)
        self.transport.on_message = self._on_message

    def _create_transport(self, additional_topics=None):
        options = self.bot.config.get_network_options()
        if not options:
            raise RuntimeError('未配置任何网络选项')
        for idx, opt in enumerate(options):
            network_type = opt.get('network', 'mqtt')
            host = opt.get('url', opt.get('host', ''))
            port = opt.get('port', 1883)
            topic = opt.get('topic', f"tll/{self.bot.config.id}")
            if network_type != 'mqtt':
                continue
            transport = MQTTTransport(
                host=host or '127.0.0.1',
                port=port,
                topic=topic,
                client_id=self.bot.config.id,
                additional_topics=additional_topics
            )
            try:
                transport.connect()
                pass
                return transport
            except Exception as e:
                pass
        raise RuntimeError('所有网络选项均连接失败')

    def _ensure_logger(self, task):
        if task.logger is None:
            create_logger(task, hook_manager=self.bot.hook_manager)
        task.logger.hook_manager = self.bot.hook_manager
        task.logger.context['sender'] = self.sender
        task.logger.context['bot_id'] = self.bot.config.id
        task.logger.context['hook_manager'] = self.bot.hook_manager
        if hasattr(self.bot, 'base_dir'):
            task.logger.context['archive_dir'] = os.path.join(self.bot.base_dir, 'tasks')
        return task.logger

    def _on_message(self, payload):
        try:
            data = json.loads(payload.decode('utf-8'))
        except Exception:
            data = None

        if isinstance(data, dict) and data.get('type') == 'TLL_EVENT':
            self.bot.hook_manager.record_external_event(data.get('event', {}))
            return

        task = self.receiver.receive(payload)
        if task is None:
            return
        pass

        logger = self._ensure_logger(task)
        logger.debug(
            f"收到消息: task_id={task.id}, from={task.from_bot}, "
            f"command={task.tlljson.command if task.tlljson else ''}, "
            f"status={task.status}"
        )

        def _handle():
            try:
                self.handle_task(task, logger)
            except Exception as e:
                try:
                    logger.error(f"处理任务异常: {e}")
                except Exception:
                    pass
            finally:
                try:
                    logger.archive(task)
                except Exception as e:
                    try:
                        logger.error(f"归档失败: {e}")
                    except Exception:
                        pass

        self._thread_pool.submit(_handle)

    def handle_task(self, task, logger):
        """子类必须实现：处理单个任务（执行、回传、回复等）"""
        raise NotImplementedError

    def start(self):
        raise NotImplementedError