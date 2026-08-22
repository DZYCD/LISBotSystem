#!/usr/bin/env python3
"""
MQTT Ping 测试脚本（带加密、TASK debug、finish hook 验证）
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tll_protocol.bot_factory import request_bot_create
from tll_protocol.mqtt_transport import MQTTTransport
from tll_protocol.receiver import TaskReceiver
from tll_protocol.executor import TaskExecutor
from tll_protocol.task_sender import TaskSender
from tll_protocol.core import TLLjson, TaskStatus, create_logger


def create_transport_from_options(config):
    options = config.get_network_options()
    if not options:
        raise RuntimeError("未配置任何网络选项")

    for idx, opt in enumerate(options):
        network_type = opt.get('network', 'mqtt')
        host = opt.get('url', opt.get('host', ''))
        port = opt.get('port', 1883)
        topic = opt.get('topic', f"tll/{config.id}")

        if network_type != 'mqtt':
            continue

        transport = MQTTTransport(
            host=host or '127.0.0.1',
            port=port,
            topic=topic,
            client_id=config.id
        )
        try:
            transport.connect()
            print(f"[传输] 已连接: {host}:{port} (选项 {idx+1}/{len(options)})")
            return transport
        except Exception as e:
            print(f"[传输] 连接失败 ({host}:{port}): {e}")

    raise RuntimeError("所有网络选项均连接失败")


class BotNode:
    def __init__(self, bot_folder):
        print(f"\n=== 初始化节点: {bot_folder} ===")
        self.bot = request_bot_create(bot_folder)
        self.transport = create_transport_from_options(self.bot.config)
        self.sender = TaskSender(transport=self.transport, bot_id=self.bot.config.id, peers=self.bot.config.peers, group=self.bot.config.group)
        self.bot.set_sender(self.sender)
        self.receiver = TaskReceiver(bot_id=self.bot.config.id, auth_key=self.bot.config.auth_key)
        self.executor = TaskExecutor(
            handler_map=self.bot.handler_map,
            sender=self.sender,
            bot_context=self.bot
        )
        self.transport.on_message = self._on_message
        print(f"节点 {self.bot.config.name} 初始化完成，ID={self.bot.config.id}")

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
        task = self.receiver.receive(payload)
        if task is None:
            print(f"[{self.bot.config.name}] 收到的 payload 无法解析为任务")
            return

        if task.id in self.bot.outgoing_tasks:
            print(f"[{self.bot.config.name}] [追踪] 这是本节点发起任务的回传")
            self.bot.complete_task(task.id, task.status)
            logger = self._ensure_logger(task)
            if task.status == TaskStatus.SUCCESS:
                print(f"[{self.bot.config.name}] [hook] 触发 SUCCESS hook")
                logger.success(f"Task {task.id} received with SUCCESS")
            elif task.status == TaskStatus.FAILED:
                print(f"[{self.bot.config.name}] [hook] 触发 ERROR hook")
                logger.error(f"Task {task.id} received with FAILED: {task.error}")
            else:
                print(f"[{self.bot.config.name}] [异常] 任务状态异常: {task.status}")
        else:
            if task.status == TaskStatus.SUCCESS or task.status == TaskStatus.FAILED:
                print(f"[{self.bot.config.name}] [回传] 收到下级回传消息")
                logger = self._ensure_logger(task)
                if task.status == TaskStatus.SUCCESS:
                    print(f"[{self.bot.config.name}] [hook] 触发 SUCCESS hook")
                    logger.success(f"Task {task.id} received with SUCCESS")
                else:
                    print(f"[{self.bot.config.name}] [hook] 触发 ERROR hook")
                    logger.error(f"Task {task.id} received with FAILED: {task.error}")
            else:
                print(f"[{self.bot.config.name}] [新任务] 执行工具")
                executed = self.executor.execute(task)
                print(f"[{self.bot.config.name}] 执行完成，状态={executed.status}")

    def start(self):
        print(f"[{self.bot.config.name}] MQTT 已连接，订阅 {self.transport.topic}")

    def send_ping(self, target):
        print(f"\n[{self.bot.config.name}] 准备发送 ping 至 {target}")
        task_id = self.bot.send_command(target, 'ping', {})
        print(f"[{self.bot.config.name}] 已发送 ping，task_id={task_id}")
        if task_id in self.bot.outgoing_tasks:
            print(f"  outgoing_tasks[{task_id}] = {self.bot.outgoing_tasks[task_id]}")

    def stop(self):
        self.transport.close()


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    node1 = BotNode(os.path.join(base, 'bots', 'sayi_996'))
    node2 = BotNode(os.path.join(base, 'bots', 'skaye_001'))
    print("\n=== 节点初始化完成 ===")
    time.sleep(1)
    print("\n=== 发送 Ping ===")
    node1.send_ping('agent/sky_001')
    time.sleep(5)
    print("\n=== 测试完成 ===")
    node1.stop()
    node2.stop()


if __name__ == '__main__':
    main()
