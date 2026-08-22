"""
TASK 归档模块 - 将任务完整信息写入指定目录。
"""

import os
import json
from datetime import datetime, timezone


def archive_task(task, archive_dir: str):
    """
    将 task 的日志、输出、链路信息归档为 JSON 文件。
    """
    try:
        os.makedirs(archive_dir, exist_ok=True)
        filename = f"{task.id}.json"
        path = os.path.join(archive_dir, filename)

        # 获取日志：合并 task.logs 与 logger.buffer，确保所有中间输出都被保存
        logs = list(getattr(task, 'logs', []) or [])
        if getattr(task, 'logger', None):
            for entry in task.logger.buffer:
                if entry not in logs:
                    logs.append(entry)

        data = {
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "task": task.to_dict(),
            "logs": logs,
            "result": task.output
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _msg = f"[ARCHIVE] Task {task.id} archived to {path}"
        if getattr(task, 'logger', None):
            task.logger.info(f"\033[38;5;208m{_msg}\033[0m")
        else:
            pass
    except Exception as e:
        pass
