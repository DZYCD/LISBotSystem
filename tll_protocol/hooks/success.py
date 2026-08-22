"""
SUCCESS hook - 处理任务成功后的最终终结日志。

回退逻辑已由 executor 自动完成，此 hook 仅传递终结信息到监控，不再归档。
"""


def success_hook(message, logger=None, task=None, **kwargs):
    if task is None or logger is None:
        return

    bot_id = logger.context.get('bot_id', '')

    # 仅当任务最终回到发起者时发送终结日志
    if task.from_bot != bot_id:
        return

    logger.finish(f"Task {task.id} delegation chain complete")
