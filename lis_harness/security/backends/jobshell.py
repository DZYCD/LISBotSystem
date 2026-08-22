"""JobObjectShell：真正的进程沙箱后端。

两层各司其职：
1. Job Object —— 管「资源配额 + 进程树治理」：真实启动子进程、限制进程数/
   内存/CPU 时间、超时强制终止。
2. SandboxPolicy —— 管「读写路径范围」：根据本次解析出的沙箱边界判断命令
   能访问哪些路径。

诚实性原则（来自 review 整改）：当前环境无受限令牌，命令无法被 OS 级路径
隔离。因此：
- 命令（command）在非 DANGER_FULL_ACCESS 档下**直接拒绝**，不假装治理。
- read 也走范围检查（allows_read），防止任意读系统文件。
- 命令输出被捕获并回传到 result（模型能看到输出）。

本类实现 CapabilityBackend seam。args 契约：
- {"command": "echo hello"}                          # 运行 shell 命令（仅 DANGER 档）
- {"write": "工作区内路径", "content": "..."}        # 工作区内写文件
- {"read": "工作区内路径"}                            # 工作区内读文件
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..capability import CapabilityBackend, ExecutionResult
from ..policy import ExecutionRequest, SandboxPolicy, SandboxMode
from .winjob import Job, JobLimits


@dataclass(frozen=True)
class JobSandboxConfig:
    """JobObjectShell 的资源约束配置。"""

    max_active_processes: int = 4
    """Job 内同时最多存活进程数。"""

    max_job_memory_bytes: Optional[int] = None
    """Job 内存上限（字节）；None 表示不限制。"""

    timeout_ms: int = 30_000
    """命令执行超时（毫秒）；超时强制终止。"""

    executable: str = ""
    """子进程可执行文件；空则默认使用当前 Python 解释器。"""


class JobObjectShell(CapabilityBackend):
    """用 Windows Job Object 治理真实子进程的 shell 后端。

    资源治理靠 Job；路径范围靠调用方传入的 SandboxPolicy（由执行管线解析）。
    命令只在 DANGER_FULL_ACCESS 下放行（当前环境无法 OS 级隔离命令路径）。
    """

    name = "shell"

    def __init__(self, config: JobSandboxConfig = JobSandboxConfig()) -> None:
        self._config = config

    async def execute(
        self,
        request: ExecutionRequest,
        policy: SandboxPolicy,
    ) -> ExecutionResult:
        args = request.arguments

        # 处理文件读写（路径范围由 SandboxPolicy 判断）
        if "write" in args:
            return self._handle_write(args, policy)
        if "read" in args:
            return self._handle_read(args, policy)

        # 处理真实命令执行（资源由 Job 治理）
        command = args.get("command")
        if command is None:
            return ExecutionResult(ok=False, error="shell: requires command, write, or read", denied=False)
        return await self._run_command(command, policy)

    # --- 文件路径范围（SandboxPolicy 层） ---

    def _handle_write(self, args: dict, policy: SandboxPolicy) -> ExecutionResult:
        raw_path = args["write"]
        content = args.get("content", "")
        path = Path(raw_path)
        if not policy.allows_write_to(path):
            return ExecutionResult(
                ok=False,
                denied=True,
                error=f"[sandbox: denied] write to {path} is outside mode {policy.mode.value}",
            )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ExecutionResult(ok=False, error=f"write failed: {exc}", denied=False)
        return ExecutionResult(ok=True, value={"effect": "write", "path": str(path)})

    def _handle_read(self, args: dict, policy: SandboxPolicy) -> ExecutionResult:
        raw_path = args["read"]
        path = Path(raw_path)
        if not policy.allows_read(path):
            return ExecutionResult(
                ok=False,
                denied=True,
                error=f"[sandbox: denied] read of {path} is outside mode {policy.mode.value}",
            )
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ExecutionResult(ok=False, error=f"read failed: {exc}", denied=False)
        return ExecutionResult(ok=True, value={"effect": "read", "path": str(path), "content": text})

    # --- 真实命令执行（Job 资源治理 + 命令路径治理） ---

    async def _run_command(self, command: str, policy: SandboxPolicy) -> ExecutionResult:
        # 诚实性原则：命令无法被当前环境 OS 级路径隔离，非 DANGER 档直接拒绝。
        if policy.mode is not SandboxMode.DANGER_FULL_ACCESS:
            return ExecutionResult(
                ok=False,
                denied=True,
                error=(
                    f"[sandbox: denied] command execution requires danger-full-access "
                    f"(current mode: {policy.mode.value}); commands cannot be path-sandboxed "
                    "in this environment"
                ),
            )
        executable = self._config.executable or os.sys.executable
        code = (
            f"import subprocess,sys,os; "
            f"r=subprocess.run({command!r}, shell=True, capture_output=True, text=True, "
            f"encoding='utf-8', errors='replace', "
            f"timeout={self._config.timeout_ms/1000}); "
            f"sys.stdout.write(r.stdout); sys.stderr.write(r.stderr); sys.exit(r.returncode)"
        )
        limits = JobLimits(
            max_active_processes=self._config.max_active_processes,
            max_job_memory_bytes=self._config.max_job_memory_bytes,
            kill_on_close=True,
        )
        job = Job(limits=limits)
        with job:
            proc = job.popen_in_job([executable, "-c", code],
                                    encoding="utf-8", errors="replace")
            try:
                stdout, stderr = proc.communicate(timeout=self._config.timeout_ms / 1000)
            except Exception:
                proc.kill()
                stdout, stderr = proc.communicate()
                return ExecutionResult(
                    ok=False,
                    denied=True,
                    error=(
                        f"[sandbox: timeout] command exceeded {self._config.timeout_ms}ms "
                        "and was force-terminated"
                    ),
                )
        if proc.returncode != 0:
            return ExecutionResult(
                ok=False,
                error=f"command exited with code {proc.returncode}: {stderr.strip()}",
                denied=False,
            )
        return ExecutionResult(ok=True, value={
            "command": command,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        })


def create(config: dict) -> CapabilityBackend:
    """实现工厂：供 PluginLoader 从 YAML 构造后端实例。"""
    timeout_ms = int(config.get("timeout_ms", 30_000))
    return JobObjectShell(JobSandboxConfig(timeout_ms=timeout_ms))

