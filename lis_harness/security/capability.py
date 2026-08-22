"""能力服务 seam（CapabilityBackend）。

对应 dsh 的 ctx.shell / ctx.fs / ctx.web —— 底层真正「干活」的能力服务。
安全套子的要点：工具不直接执行，而是调用能力后端；能力后端在收到本次
解析出的 SandboxPolicy 后，只在范围内执行。

沙箱不是内嵌在工具里，而是包在能力后端外面 —— 换一个后端（如 fs-local
→ fs-sandbox）就能改变整个系统的安全边界，工具代码不用改。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from .policy import ExecutionRequest, SandboxPolicy


@dataclass(frozen=True)
class ExecutionResult:
    """一次受保护执行的结果。"""

    ok: bool
    """是否成功执行。"""

    value: Any = None
    """执行产出（模型可见）。"""

    error: Optional[str] = None
    """失败时的人类可读错误说明。"""

    denied: bool = field(default=False)
    """是否因超出沙箱范围而被拒绝（而非执行失败）。"""


class CapabilityBackend(ABC):
    """一个能力服务的抽象。

    子类实现一个具体能力（shell / fs / web）。关键契约：
    - execute 收到本次解析出的 SandboxPolicy，必须自行判断操作是否在范围内；
    - 超出范围的操作应返回 denied=True，而不是直接执行。
    """

    name: str
    """能力名（如 'shell'、'fs'）。"""

    @abstractmethod
    async def execute(
        self,
        request: ExecutionRequest,
        policy: SandboxPolicy,
    ) -> ExecutionResult:
        """在给定沙箱边界内执行一次操作。

        Args:
            request: 一次执行请求（含工具名、参数、发起者）。
            policy: 本次解析出的沙箱边界；子类据此判断范围。

        Returns:
            执行结果。超范围的操作返回 denied=True。
        """
        raise NotImplementedError
