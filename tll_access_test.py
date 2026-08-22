#!/usr/bin/env python3
"""
TLL 工具访问控制测试

测试用例：
1. sayi_996 调用 skaye_001 的 ping -> 预期 FAILED (ping 仅 Skaye 可用)
2. skaye_001 调用 sayi_996 的 ping -> 预期 SUCCESS (Skaye 有权限)
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
from tll_protocol.core import TaskStatus, create_logger


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
        self.sender = TaskSender(
            transport=self.transport,
            bot_id=self.bot.config.id,
            peers=self.bot.config.peers,
            group=self.bot.config.group
        )
        self.bot.set_sender(self.sender)
        self.receiver = TaskReceiver(bot_id=self.bot.config.id, auth_key=self.bot.config.auth_key)
        self.executor = TaskExecutor(
            handler_map=self.bot.handler_map,
            sender=self.sender,
            bot_context=self.bot
        )
        self.transport.on_message = self._on_message
        print(f"节点 {self.bot.config.name} 初始化完成，ID={self.bot.config.id}，组={self.bot.config.group}")

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
            print(f"[{self.bot.config.name}] [追踪] 收到任务回传 {task.id}")
            self.bot.complete_task(task.id, task.status)
            logger = self._ensure_logger(task)
            if task.status == TaskStatus.SUCCESS:
                logger.success(f"Task {task.id} received with SUCCESS")
            elif task.status == TaskStatus.FAILED:
                logger.error(f"Task {task.id} received with FAILED: {task.error}")
            else:
                print(f"[{self.bot.config.name}] 任务 {task.id} 状态异常: {task.status}")
        else:
            if task.status == TaskStatus.SUCCESS or task.status == TaskStatus.FAILED:
                logger = self._ensure_logger(task)
                if task.status == TaskStatus.SUCCESS:
                    logger.success(f"Task {task.id} received with SUCCESS")
                else:
                    logger.error(f"Task {task.id} received with FAILED: {task.error}")
            else:
                print(f"[{self.bot.config.name}] 收到新任务 {task.id}: {task.tlljson.command}")
                executed = self.executor.execute(task)
                print(f"[{self.bot.config.name}] 执行完成，状态={executed.status}")

    def send_command(self, target, command, params=None):
        return self.bot.send_command(target, command, params or {})

    def get_task_status(self, task_id):
        rec = self.bot.outgoing_tasks.get(task_id)
        if not rec:
            return None
        return rec.get('status')

    def stop(self):
        self.transport.close()


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    sayi = BotNode(os.path.join(base, 'bots', 'sayi_996'))
    skaye = BotNode(os.path.join(base, 'bots', 'skaye_001'))

    print("\n=== 测试开始 ===")

    # 用例1: sayi_996 ping skaye_001 -> 预期 FAILED
    print("\n--- 用例1: sayi_996 -> skaye_001 ping (预期 FAILED) ---")
    task1 = sayi.send_command('agent/sky_001', 'ping', {})
    print(f"task_id={task1}")

    # 用例2: skaye_001 ping sayi_996 -> 预期 SUCCESS
    print("\n--- 用例2: skaye_001 -> sayi_996 ping (预期 SUCCESS) ---")
    task2 = skaye.send_command('agent/sayi_996', 'ping', {})
    print(f"task_id={task2}")

    # 等待消息往返
    print("\n等待消息往返...")
    time.sleep(6)

    status1 = sayi.get_task_status(task1)
    status2 = skaye.get_task_status(task2)
    print(f"\n用例1 状态: {status1} (预期 FAILED)")
    print(f"用例2 状态: {status2} (预期 SUCCESS)")

    pass1 = status1 == TaskStatus.FAILED
    pass2 = status2 == TaskStatus.SUCCESS

    print("\n=== 测试结果 ===")
    print(f"用例1: {'PASS' if pass1 else 'FAIL'}")
    print(f"用例2: {'PASS' if pass2 else 'FAIL'}")

    sayi.stop()
    skaye.stop()

    if pass1 and pass2:
        print("\n全部通过 ✅")
        return 0
    else:
        print("\n存在未通过项 ❌")
        return 1


if __name__ == '__main__':
    sys.exit(main())
