"""审批服务：一次受保护执行在放行前是否需要用户批准。

对应 dsh 的 ctx.approval（approval/request 通道）。核心是：
- 审批发生在执行之前（pre-execute 阶段），由执行管线触发。
- 批准的语义通常是「一次性」（allowed-once），不是永久解锁。
- 工具自身不内置审批逻辑；审批是挂在共享层的横切服务。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from .modes import SandboxMode
from .policy import ExecutionRequest


class ApprovalOutcome(Enum):
    """一次审批请求的三种终态。"""

    ALLOWED_ONCE = "allowed-once"
    """批准本次调用（一次性，不形成持久授权）。"""

    REJECTED = "rejected"
    """用户拒绝本次调用。"""

    CANCELLED = "cancelled"
    """审批被取消（如调用方中止）。"""


@dataclass(frozen=True)
class ApprovalResult:
    """一次审批的决策结果。"""

    outcome: ApprovalOutcome
    """决策终态。"""

    reason: Optional[str] = None
    """可选的人类可读说明。"""

    @property
    def is_allowed(self) -> bool:
        return self.outcome is ApprovalOutcome.ALLOWED_ONCE


# 审批决策回调的签名：给出一段用户可读的描述，返回决策。
ApprovalPrompt = Callable[[ExecutionRequest, str], ApprovalOutcome]


class ApprovalService(ABC):
    """审批服务抽象。

    具体实现负责把「需要审批的调用」呈现给用户并收集决策，例如：
    - 交互式控制台 / 弹窗提示；
    - 通过 transport（如 MQTT 回发）把审批请求推给用户，再等回复。
    """

    @abstractmethod
    async def request(
        self,
        request: ExecutionRequest,
        reason: str,
        requested_mode: Optional[SandboxMode] = None,
    ) -> ApprovalResult:
        """请求对一次调用的审批。

        Args:
            request: 待审批的执行请求。
            reason: 为什么需要审批（给用户的说明）。
            requested_mode: 该调用想升级到的档位（如超出默认范围）。

        Returns:
            审批决策。
        """
        raise NotImplementedError


class CallbackApprovalService(ApprovalService):
    """基于回调的审批服务：把审批请求交给一个可注入的决策函数。

    测试和简单接入用它最方便 —— 只需提供一个返回 ApprovalOutcome 的函数。
    生产环境应换成真正与用户交互的实现。
    """

    def __init__(self, prompt: ApprovalPrompt) -> None:
        self._prompt = prompt

    async def request(
        self,
        request: ExecutionRequest,
        reason: str,
        requested_mode: Optional[SandboxMode] = None,
    ) -> ApprovalResult:
        outcome = self._prompt(request, reason)
        return ApprovalResult(outcome=outcome)
