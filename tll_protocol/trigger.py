#!/usr/bin/env python3
"""
统一扳机模块 - 为网络大屏提供 ping 和 chat 两个动作入口

用法：
    from tll_protocol.trigger import create_ping_handler, create_chat_handler, send_ping

    # 在 Skaye_SV 进程中
    ping_handler = create_ping_handler(bot)
    # 将 ping_handler 传给 dashboard 的 ping_callback

    # 在 SaYi_SV 进程中
    chat_handler = create_chat_handler(bot)
    # 在 HTTP 或 MQTT 入口调用 chat_handler(bot_id, text)
"""
from datetime import datetime


def send_ping(bot, target):
    """向指定机器人发送 ping，供定时调度和手动扳机共用。"""
    if not hasattr(bot, 'ping_status'):
        bot.ping_status = {}
    pass
    t = bot.create_task(target, commands=[{'target': target, 'command': 'ping', 'params': {}}], on_archive=lambda tk, target=target: _on_ping_return(target, tk))
    t.bot = bot
    bot.send_command(t)
    return {'task_id': t.id, 'target': target}


def _on_ping_return(target, task):
    """归档前回调：保存 ping 信息并更新 bot 状态"""
    if not hasattr(task, 'bot'):
        return
    bot = task.bot
    if not hasattr(bot, 'ping_status'):
        bot.ping_status = {}
    bot.ping_status[target] = {
        'last_check': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'status': task.status.value if hasattr(task.status, 'value') else str(task.status),
        'output': task.output,
        'error': task.error
    }
    try:
        from record_lis.tool import update_last_handshake
        update_last_handshake(target)
    except Exception as e:
        pass
    pass


def create_ping_handler(bot):
    """创建手动 ping 扳机。

    Args:
        bot: Skaye_SV 的 bot 实例（具备 create_task/send_command 能力）

    Returns:
        function(target: str) -> dict
    """
    def ping_handler(target):
        result = send_ping(bot, target)
        return {'status': 'sent', 'bot_id': target, 'info': result}
    return ping_handler


def create_chat_handler(bot):
    """创建聊天扳机（异步：发送任务后立即返回，不等待回复）。

    Args:
        bot: SaYi_SV 的 bot 实例（具备 send_chat 方法）

    Returns:
        function(target: str, text: str) -> dict
    """
    def chat_handler(target, text):
        if not hasattr(bot, 'send_chat'):
            return {'status': 'error', 'info': 'bot 不支持 send_chat 方法'}
        task_id = bot.send_chat(target, text)
        return {'status': 'sent', 'task_id': task_id}
    return chat_handler
