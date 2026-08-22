"""
TASK 输入模块 - LIS v2

根据架构 2.10：支持三种创建来源：
1. API：通过前端、单片机、麦克风等外接设备对话产生的输入。
2. SaYi 自创建：SaYi 内部逻辑自然创建的任务。
3. SV 委托：SuperVisor（蚕豆）委托给 SaYi 的任务。

所有方法统一返回标准 Task 对象，已填充 tlljson 和 trace。
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Union

from .core import Task, TLLjson, create_task


class TaskInputModule:
    """TASK 输入模组"""

    def __init__(self, bot_id: str, bot_config: Optional[Dict[str, Any]] = None):
        """
        Args:
            bot_id: 当前 SaYi 的机器人 ID（创立者）
            bot_config: 机器人配置，用于创建 logger 时挂载 hooks
        """
        self.bot_id = bot_id
        self.bot_config = bot_config or {}

    def create_from_api(self, api_request: Dict[str, Any]) -> Task:
        """
        从 API 输入创建 TASK。

        示例 api_request:
        {
            "task_type": "dialog",
            "command": "chat",
            "params": {"text": "你好"},
            "to": "",  # 可选，指定目标机器人
            "auth": "user-token",
            "check_tools": ["api_auth"]  # 可选
        }
        """
        from_bot = self.bot_id
        task_type = api_request.get("task_type", "dialog")
        command = api_request.get("command", "")
        params = api_request.get("params", {})
        to = api_request.get("to", "")
        auth = api_request.get("auth", "")
        task_func = api_request.get("task_func", command or "process")
        check_tools = api_request.get("check_tools", [])

        tll = TLLjson(
            version="2.0",
            from_bot=from_bot,
            command=command,
            to=to,
            task_func=task_func,
            params=params,
            auth=auth,
            check_tools=check_tools
        )

        task = create_task(
            task_type=task_type,
            from_bot=from_bot,
            current_agent=from_bot,
            tlljson=tll,
            bot_config=self.bot_config
        )
        task.trace.add_hop("api_gateway", "receive", "pending")
        return task

    def create_from_internal(self, task_type: str,
                             params: Optional[Dict[str, Any]] = None,
                             command: Optional[str] = None,
                             auth: str = "internal") -> Task:
        """
        从 SaYi 内部自动创建 TASK（如定时自检、主动学习）。

        Args:
            task_type: 任务类型（routine、dialog、tool 等）
            params: 任务参数
            command: 指令名，默认与 task_type 相同
            auth: 鉴权标识，自建任务通常为 internal
        """
        from_bot = self.bot_id
        command = command or task_type
        params = params or {}

        tll = TLLjson(
            version="2.0",
            from_bot=from_bot,
            command=command,
            to="",
            task_func=command,
            params=params,
            auth=auth
        )

        task = create_task(
            task_type=task_type,
            from_bot=from_bot,
            current_agent=from_bot,
            tlljson=tll,
            bot_config=self.bot_config
        )
        task.trace.add_hop(from_bot, "internal_create", "pending")
        return task

    def create_from_sv(self, instruction: Union[str, Dict[str, Any]],
                       auth: str = "sv-auth") -> Task:
        """
        从 SuperVisor (蚕豆) 委托创建 TASK。

        Args:
            instruction: 指令，可以是字符串或字典。字典示例：
                {
                    "task_type": "research",
                    "command": "search",
                    "params": {"query": "xxx"},
                    "to": ""
                }
            auth: SV 的鉴权凭据
        """
        from_bot = self.bot_id

        if isinstance(instruction, dict):
            task_type = instruction.get("task_type", "general")
            command = instruction.get("command", "")
            params = instruction.get("params", {})
            to = instruction.get("to", "")
            task_func = instruction.get("task_func", command or "process")
        else:
            # 简单文本指令，统一封装为命令
            task_type = "sv_command"
            command = "execute"
            params = {"command_text": instruction}
            to = ""
            task_func = "execute"

        tll = TLLjson(
            version="2.0",
            from_bot=from_bot,
            command=command,
            to=to,
            task_func=task_func,
            params=params,
            auth=auth
        )

        task = create_task(
            task_type=task_type,
            from_bot=from_bot,
            current_agent=from_bot,
            tlljson=tll,
            bot_config=self.bot_config
        )
        task.trace.add_hop("sv", "delegate", "pending")
        return task


# 简单测试
if __name__ == "__main__":
    im = TaskInputModule(bot_id="agent/sayi_996")

    # API 输入
    api_task = im.create_from_api({"task_type": "dialog", "command": "chat", "params": {"text": "hello"}})
    pass

    # 内部创建
    internal_task = im.create_from_internal("routine", {"name": "daily_report"})
    pass

    # SV 委托
    sv_task = im.create_from_sv({"task_type": "research", "command": "search", "params": {"query": "LIS"}})
    pass
