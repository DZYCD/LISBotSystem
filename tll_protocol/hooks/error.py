"""
error 级别 hook：错误回退逻辑

- 若 task 有 route，则按 route 回退到上一跳
- 若 route 为空，回退到 from_bot
- 若已回到创建者，触发 finish 归档
"""


from datetime import datetime


def error_hook(message, logger=None, task=None, **kwargs):
    if task is None or logger is None:
        return

    sender = logger.context.get('sender')
    bot_id = logger.context.get('bot_id', '')

    if sender is None:
        return

    # 优先使用 route 回退
    if task.route:
        target = task.route.pop()

        # 如果目标是自己，继续寻找非自己
        if target == bot_id:
            while task.route and target == bot_id:
                target = task.route.pop()
            # 如果最终目标仍是自己且创建者也是自己，说明已回退到底
            if target == bot_id:
                if task.from_bot == bot_id:
                    result = logger.finish(f"Task {task.id} error chain complete")
                    if result is not True:
                        logger.archive(task)
                return

        try:
            sender.send_task(task, target, push_route=False)
        except Exception:
            pass
        return

    # route 为空，回退给创建者
    target = task.from_bot
    if not target:
        return

    if target == bot_id:
        result = logger.finish(f"Task {task.id} error chain complete")
        if result is not True:
            logger.archive(task)
        return

    try:
        sender.send_task(task, target, push_route=False)
    except Exception:
        pass
