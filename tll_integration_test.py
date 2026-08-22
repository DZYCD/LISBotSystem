#!/usr/bin/env python3
"""TLL 协议集成测试：验证完整生命周期"""

import os
import sys
import time

# 确保能导入 tll_protocol
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tll_protocol import (
    request_bot_create, create_task, TaskExecutor, TaskSender, TLLjson,
    hook_manager
)


def main():
    print("=== TLL 集成测试开始 ===")

    # 1. 测试 Bot 创建
    bot_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bots', 'sayi_996')
    print(f"\n[1] 从 {bot_folder} 创建 Bot")
    bot = request_bot_create(bot_folder)
    print(f"    Bot: {bot.config.name} ({bot.config.id}), status={bot.status}")
    assert bot.config.name == 'sayi_996'

    # 2. 创建 TASK
    print("\n[2] 创建 TASK")
    tlljson = TLLjson(
        from_bot='agent/sayi_996',
        command='greet',
        to='agent/sayi_998',
        params={'name': '蚕豆'}
    )
    task = create_task('dialog', 'agent/sayi_996', 'agent/sayi_996', tlljson)
    print(f"    TASK id: {task.id}, type: {task.type}")

    # 3. 执行 TASK
    print("\n[3] 执行 TASK")
    def greet_handler(params, task):
        return f"Hello, {params.get('name', 'world')}!"

    sender = TaskSender()  # 无 transport，不会真正发送
    executor = TaskExecutor(handler_map={'greet': greet_handler}, sender=sender)
    executed = executor.execute(task)
    print(f"    状态: {executed.status}, 输出: {executed.output}")
    assert executed.status.value == 'success'

    # 4. 检查 hook 监控
    print("\n[4] Hook 监控事件")
    events = hook_manager.get_recent_events(limit=10)
    for e in events:
        print(f"    [{e['level']}] {e['hook_name']} - {e['status']} - {e['task_info'].get('task_id')}")
    print("\n=== TLL 集成测试结束 ===")


if __name__ == '__main__':
    main()
