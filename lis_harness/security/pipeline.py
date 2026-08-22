"""受保护执行管线（ExecutionPipeline）。

对应 dsh 的 ToolRuntime 执行管线。这是安全套子的枢纽，把审批、策略解析、
能力执行串成一条顺序，确保任何工具调用都经过同一套治理：

    pre-execute(审批决策: allow/deny/ask)
      → 若 ask: 走审批服务，批准则注入升级档
      → 解析本次 SandboxPolicy（每调用动态解析）
      → 调能力后端在范围内执行
      → 返回结果

安全逻辑全部在此管线内，工具自身不内置 —— 这是「工具扩展能力、审批治理能力」
的落点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

from .approval import ApprovalService, ApprovalOutcome
from .capability import CapabilityBackend, ExecutionResult
from .modes import SandboxMode
from .policy import ExecutionRequest, SandboxPolicy, SandboxPolicyResolver


class PreToolDecision(Enum):
    """pre-execute 阶段对一次调用的初步决策。"""

    ALLOW = "allow"
    """放行（在解析出的默认范围内执行）。"""

    ASK = "ask"
    """需要审批：放行前先问用户。"""

    DENY = "deny"
    """直接拒绝。"""


@dataclass(frozen=True)
class PreExecuteVerdict:
    """pre-execute 阶段的决策结果。"""

    decision: PreToolDecision
    reason: Optional[str] = None
    """给用户的说明（ask/deny 时）。"""

    requested_mode: Optional[SandboxMode] = None
    """ask 时希望升级到的档位。"""


# pre-execute 监听器：可对一次调用给出 allow/ask/deny 决策。
PreExecuteListener = Callable[[ExecutionRequest], PreExecuteVerdict]


@dataclass
class ExecutionPipeline:
    """受保护执行管线。

    Args:
        policy_resolver: 每次调用解析沙箱边界的解析器。
        approval: 审批服务。ask 决策会走它。
        default_verdict: 未注册监听器时的默认决策（默认 ALLOW，在默认范围内跑）。
    """

    policy_resolver: SandboxPolicyResolver
    approval: ApprovalService
    default_verdict: PreExecuteVerdict = field(
        default_factory=lambda: PreExecuteVerdict(PreToolDecision.ALLOW)
    )
    _listeners: List[PreExecuteListener] = field(default_factory=list)

    def add_pre_execute_listener(self, listener: PreExecuteListener) -> None:
        """注册一个 pre-execute 监听器，可对调用给出 allow/ask/deny。

        监听器是扩展点：权限预设、工具级默认审批策略等都可在这里挂。
        """
        self._listeners.append(listener)

    def _pre_execute(self, request: ExecutionRequest) -> PreExecuteVerdict:
        """跑 pre-execute 监听器链，返回首个非 ALLOW 的决策（否则默认）。"""
        for listener in self._listeners:
            verdict = listener(request)
            if verdict.decision is not PreToolDecision.ALLOW:
                return verdict
        return self.default_verdict

    async def execute(
        self,
        request: ExecutionRequest,
        backend: CapabilityBackend,
    ) -> ExecutionResult:
        """受保护地执行一次工具调用。

        完整顺序：
        1. pre-execute 决策（allow/ask/deny）
        2. 若 ask，走审批服务；批准则把升级档作为 escalation
        3. 解析本次沙箱边界
        4. 在边界内调能力后端执行
        """
        verdict = self._pre_execute(request)
        if verdict.decision is PreToolDecision.DENY:
            return ExecutionResult(
                ok=False,
                error=verdict.reason or f'tool "{request.tool_name}" was denied before execution',
                denied=True,
            )

        escalation: Optional[SandboxMode] = None
        if verdict.decision is PreToolDecision.ASK:
            approval_result = await self.approval.request(
                request,
                verdict.reason or f'tool "{request.tool_name}" requires approval',
                requested_mode=verdict.requested_mode,
            )
            if not approval_result.is_allowed:
                return ExecutionResult(
                    ok=False,
                    error=approval_result.reason
                        or f'tool "{request.tool_name}" was {approval_result.outcome.value} by the user',
                    denied=True,
                )
            escalation = verdict.requested_mode

        policy = self.policy_resolver.resolve(request, escalation=escalation)
        return await backend.execute(request, policy)
