"""策略层：一次执行请求与沙箱策略的每调用动态解析。

核心思想（来自 dsh 机制研究）：
- 沙箱边界不是启动时一次性定死，而是每次工具调用时重新解析。
- 解析顺序：已获审批的调用级升级档 > 会话级覆盖 > 全局默认档。
- 范围通过 SandboxPolicy（模式 + 工作区根）表达，能力后端据此判断具体操作是否允许。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .modes import SandboxMode


@dataclass(frozen=True)
class ExecutionRequest:
    """一次受保护执行的请求。

    模型输出 tool-call 之后、进入执行管线前被构造成的描述对象。
    能力后端和审批服务都基于它判断。
    """

    tool_name: str
    """请求调用的工具名。"""

    arguments: dict
    """模型给出的参数（已解析为 dict）。"""

    actor: str
    """发起这次调用的主体标识（如会话 id）。"""

    requested_mode: Optional[SandboxMode] = None
    """工具主动请求的沙箱档位（可为空 = 用解析默认档）。"""


@dataclass(frozen=True)
class SandboxPolicy:
    """一次执行解析出的沙箱边界。

    由默认档 + 本次调用的升级决定。能力后端用它判断某个具体操作是否在范围内。
    """

    mode: SandboxMode
    """本次执行生效的范围档位。"""

    workspace_root: Optional[Path] = None
    """工作区根路径；WORKSPACE_WRITE 档用它界定可写范围。"""

    def allows_write_to(self, path: Path) -> bool:
        """判断对 path 的写是否在本次范围内。

        规则：
        - DANGER_FULL_ACCESS：任意路径允许。
        - READ_ONLY：一律拒绝写。
        - WORKSPACE_WRITE：只有 workspace_root 之内的路径允许；未设根则拒绝。
        """
        if self.mode is SandboxMode.DANGER_FULL_ACCESS:
            return True
        if not self.mode.allows_write:
            return False
        if self.workspace_root is None:
            return False
        try:
            path.resolve().relative_to(self.workspace_root.resolve())
            return True
        except ValueError:
            return False

    def allows_read(self, path: Path) -> bool:
        """判断对 path 的读是否在本次范围内。

        语义：读默认与写同范围（限定在工作区），防止任意读取系统敏感文件。
        - DANGER_FULL_ACCESS：任意路径允许读。
        - 其他档位：只有 workspace_root 之内允许读；未设根则拒绝。
        """
        if self.mode is SandboxMode.DANGER_FULL_ACCESS:
            return True
        if self.workspace_root is None:
            return False
        try:
            path.resolve().relative_to(self.workspace_root.resolve())
            return True
        except ValueError:
            return False


@dataclass(frozen=True)
class SandboxPolicyResolver:
    """沙箱策略的每调用解析器。

    持有全局默认档、会话级覆盖档与工作区根。每次 resolve 结合「已获审批的
    调用级升级 + 会话覆盖 + 全局默认」产出该次调用的实际策略。
    """

    default_mode: SandboxMode
    """全局默认档位（大致对应「选定工作区」时确定的基准档）。"""

    workspace_root: Optional[Path] = None
    """工作区根路径，供 WORKSPACE_WRITE 档界定范围。"""

    session_override: Optional[SandboxMode] = None
    """会话级覆盖档；不为空时覆盖 default_mode。"""

    def resolve(
        self,
        request: ExecutionRequest,
        escalation: Optional[SandboxMode] = None,
    ) -> SandboxPolicy:
        """为一次请求解析出实际生效的沙箱边界。

        解析优先级（从高到低）：
        1. escalation —— 已获审批的调用级升级档，总是生效（更权威）。
        2. session_override —— 会话级覆盖。
        3. default_mode —— 全局默认。

        注意：request.requested_mode 只是「工具想要的档位」，本解析器不据此
        自动提权 —— 是否放行由执行管线走审批决定。审批通过后以 escalation
        形式注入。
        """
        base = self.session_override or self.default_mode
        mode = escalation if escalation is not None else base
        return SandboxPolicy(mode=mode, workspace_root=self.workspace_root)
