"""InProcessShell：进程内模拟 shell（演示用途）。

不起真实进程，只做范围判断，演示「能力后端如何用 SandboxPolicy 判断路径范围」。
真实执行请用 JobObjectShell。保留它是为了在不启动进程的情况下教学/测试
路径范围判断逻辑。

args 契约：
- {"op": "write", "path": "...", "content": "..."}
- {"op": "read", "path": "..."}
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..capability import CapabilityBackend, ExecutionResult
from ..policy import ExecutionRequest, SandboxPolicy


@dataclass(frozen=True)
class ShellSideEffect:
    """一条被模拟的命令副作用。"""

    kind: str
    path: Path
    content: Optional[str] = None


class InProcessShell(CapabilityBackend):
    name = "shell"

    async def execute(
        self,
        request: ExecutionRequest,
        policy: SandboxPolicy,
    ) -> ExecutionResult:
        op = request.arguments.get("op")
        raw_path = request.arguments.get("path")
        if op is None or raw_path is None:
            return ExecutionResult(ok=False, error="shell: requires op and path", denied=False)

        path = Path(raw_path)
        if op == "write":
            if not policy.allows_write_to(path):
                return ExecutionResult(
                    ok=False,
                    denied=True,
                    error=f"[sandbox: denied] write to {path} is outside mode {policy.mode.value}",
                )
            return ExecutionResult(
                ok=True,
                value={
                    "effect": "write",
                    "path": str(path),
                    "mode": policy.mode.value,
                    "workspace_root": str(policy.workspace_root) if policy.workspace_root else None,
                },
            )
        if op == "read":
            return ExecutionResult(
                ok=True,
                value={
                    "effect": "read",
                    "path": str(path),
                    "mode": policy.mode.value,
                    "workspace_root": str(policy.workspace_root) if policy.workspace_root else None,
                },
            )
        return ExecutionResult(ok=False, error=f"shell: unknown op {op}", denied=False)
