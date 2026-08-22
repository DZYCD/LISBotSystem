"""Windows Job Object（作业对象）的 ctypes 封装。

Job Object 是 Windows 的资源隔离原语，不需要特殊特权即可使用。它把一组
进程放进一个「作业」里统一治理：
- 资源配额：活动进程数上限、CPU 时间上限、Job 内存上限。
- 进程树治理：Job 关闭时自动终止进程树（KILL_ON_JOB_CLOSE），可显式
  TerminateJobObject 强制终止。

注意：Job Object 管的是「资源配额」，不管「文件读写路径」。路径范围由
SandboxPolicy 那层负责。两层各司其职，本模块只负责资源与进程树治理。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

if sys.platform != "win32":  # pragma: no cover - 非 Windows 平台不可用
    raise ImportError("winjob requires Windows")

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- Job Object 常量 ---
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0008
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x0200
JOB_OBJECT_LIMIT_PROCESS_TIME = 0x0002
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x0100

JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_QUERY_LIMIT_VIOLATION_INFORMATION = 13

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000

# 常用 Job 信息类常量
JobObjectBasicLimitInformation = 2
JobObjectExtendedLimitInformation = 9
JobObjectBasicAccountingInformation = 1
JobObjectEndOfJobTimeInformation = 6
JobObjectLimitViolationInformation = 13

INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
STILL_ACTIVE = 259


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.POINTER(wt.LPVOID)),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wt.DWORD),
        ("TotalProcesses", wt.DWORD),
        ("ActiveProcesses", wt.DWORD),
        ("TotalTerminatedProcesses", wt.DWORD),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD),
        ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD),
        ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.POINTER(wt.BYTE)),
        ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE),
        ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
    ]


# --- API 签名 ---
_CreateJobObjectW = _kernel32.CreateJobObjectW
_CreateJobObjectW.argtypes = [wt.LPVOID, wt.LPCWSTR]
_CreateJobObjectW.restype = wt.HANDLE

_SetInformationJobObject = _kernel32.SetInformationJobObject
_SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
_SetInformationJobObject.restype = wt.BOOL

_AssignProcessToJobObject = _kernel32.AssignProcessToJobObject
_AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
_AssignProcessToJobObject.restype = wt.BOOL

_TerminateJobObject = _kernel32.TerminateJobObject
_TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
_TerminateJobObject.restype = wt.BOOL

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wt.HANDLE]
_CloseHandle.restype = wt.BOOL

_CreateProcessW = _kernel32.CreateProcessW
_CreateProcessW.argtypes = [
    wt.LPCWSTR, wt.LPCWSTR, wt.LPVOID, wt.LPVOID, wt.BOOL,
    wt.DWORD, wt.LPVOID, wt.LPCWSTR,
    ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION),
]
_CreateProcessW.restype = wt.BOOL

_ResumeThread = _kernel32.ResumeThread
_ResumeThread.argtypes = [wt.HANDLE]
_ResumeThread.restype = wt.DWORD

_WaitForSingleObject = _kernel32.WaitForSingleObject
_WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_WaitForSingleObject.restype = wt.DWORD

_GetExitCodeProcess = _kernel32.GetExitCodeProcess
_GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
_GetExitCodeProcess.restype = wt.BOOL

_GetCurrentProcess = _kernel32.GetCurrentProcess
_GetCurrentProcess.restype = wt.HANDLE


@dataclass(frozen=True)
class JobLimits:
    """Job Object 的资源配额。

    全部为可选；None 表示不施加该限制。
    """

    max_active_processes: Optional[int] = None
    """Job 内同时最多存活的活动进程数。"""

    max_job_memory_bytes: Optional[int] = None
    """Job 内所有进程合计的内存上限（字节）。"""

    max_process_time_ms: Optional[int] = None
    """单个进程的 CPU 时间上限（毫秒）。"""

    kill_on_close: bool = True
    """Job 关闭时是否自动终止进程树。True 防止孤儿进程残留。"""


@dataclass(frozen=True)
class JobRunResult:
    """一次 Job 内运行的结果。"""

    exit_code: int
    """进程退出码。"""

    timed_out: bool = False
    """是否因超时被 TerminateJobObject 强制终止。"""

    terminated_by_job: bool = False
    """是否因 Job 策略（KILL_ON_JOB_CLOSE / 资源超限）被终止。"""


class Job:
    """一个 Windows Job Object 句柄的持有者。

    负责创建 Job、施加资源配额、把子进程分配进去，以及（可选）在 Job
    关闭时自动终止进程树。实现可用的协议：

    - with Job(limits=...) as job: ...
    - job.spawn(executable, args, cwd) -> (process_handle, pid)
    - job.terminate() 强制终止进程树
    """

    def __init__(self, limits: JobLimits = JobLimits()) -> None:
        self._limits = limits
        self._handle: Optional[int] = None
        self._closed = False

    # --- 生命周期 ---

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Job already closed")
        if self._handle is not None:
            return
        handle = _CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        self._handle = handle
        self._apply_limits()

    def _apply_limits(self) -> None:
        if self._handle is None:
            return
        flags = 0
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        if self._limits.kill_on_close:
            flags |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if self._limits.max_active_processes is not None:
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = self._limits.max_active_processes
        if self._limits.max_job_memory_bytes is not None:
            flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = self._limits.max_job_memory_bytes
        if self._limits.max_process_time_ms is not None:
            flags |= JOB_OBJECT_LIMIT_PROCESS_TIME
            # PerProcessUserTimeLimit 单位是 100ns
            info.BasicLimitInformation.PerProcessUserTimeLimit = self._limits.max_process_time_ms * 10_000
        info.BasicLimitInformation.LimitFlags = flags
        if flags == 0:
            return
        ok = _SetInformationJobObject(
            self._handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

    # --- 进程管理 ---

    def spawn(self, executable: str, args: list[str], cwd: Optional[str] = None) -> int:
        """在 Job 内启动一个挂起子进程。

        流程：CreateProcess(CREATE_SUSPENDED) → AssignProcessToJobObject →
        ResumeThread。用挂起状态先分配进 Job，保证进程一开始就在治理范围内。

        Args:
            executable: 可执行文件路径。
            args: 命令行参数（不含可执行文件本身）。
            cwd: 工作目录。

        Returns:
            子进程 PID。

        Raises:
            OSError: 启动或分配失败。
        """
        self._ensure_open()
        cmdline = subprocess.list2cmdline([executable, *args])
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        pi = PROCESS_INFORMATION()
        creation_flags = CREATE_SUSPENDED | CREATE_NO_WINDOW
        ok = _CreateProcessW(
            executable,
            cmdline,
            None,
            None,
            False,
            creation_flags,
            None,
            cwd,
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "CreateProcessW failed")
        try:
            assigned = _AssignProcessToJobObject(self._handle, pi.hProcess)
            if not assigned:
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        except Exception:
            _TerminateJobObject(self._handle, 1)
            _CloseHandle(pi.hProcess)
            _CloseHandle(pi.hThread)
            raise
        _ResumeThread(pi.hThread)
        pid = int(pi.dwProcessId)
        # 关闭子进程句柄；Job 持有所属权，我们不需要直接持有句柄
        _CloseHandle(pi.hProcess)
        _CloseHandle(pi.hThread)
        return pid

    def wait(self, pid: int, timeout_ms: int, command_name: str = "") -> JobRunResult:
        """等待子进程结束，超时则用 Job 强制终止进程树。

        Args:
            pid: 要等待的进程 PID（spawn 返回值）。
            timeout_ms: 超时毫秒；0 表示不等待。Infinity 需要单独处理。
            command_name: 进程命令名（用于错误信息）。

        Returns:
            运行结果。超时被终止时 timed_out=True。
        """
        if timeout_ms <= 0:
            return JobRunResult(exit_code=STILL_ACTIVE)
        # 打开进程句柄等待。pid 可能已结束，需容错。
        # 需要 SYNCHRONIZE 权限才能 WaitForSingleObject；PROCESS_QUERY_LIMITED_INFORMATION 读取退出码。
        PROCESS_SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = _kernel32.OpenProcess(PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # 进程已消失
            return JobRunResult(exit_code=0)
        try:
            wait_result = _WaitForSingleObject(handle, timeout_ms)
            if wait_result == WAIT_TIMEOUT:
                self.terminate()
                return JobRunResult(exit_code=0, timed_out=True)
            exit_code = wt.DWORD()
            _GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return JobRunResult(exit_code=exit_code.value)
        finally:
            _CloseHandle(handle)

    def popen_in_job(
        self,
        args: list[str],
        cwd: Optional[str] = None,
        capture_output: bool = True,
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
    ):
        """用 subprocess.Popen 启动子进程（支持输出捕获），并尝试 Assign 进 Job。

        用 Popen 而非手写 CreateProcess，是为了原生获得 stdout/stderr 管道。
        Windows 8+ 可能已把进程放进另一个 Job 导致 Assign 失败——此时降级为
        Popen 自身治理（超时用 kill）。返回的 Popen 对象可直接 communicate。

        Args:
            args: 完整命令行（[executable, ...]）。
            cwd: 工作目录。
            capture_output: 是否捕获 stdout/stderr。

        Returns:
            subprocess.Popen 对象。
        """
        self._ensure_open()
        stdout = subprocess.PIPE if capture_output else None
        stderr = subprocess.PIPE if capture_output else None
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=stdout,
            stderr=stderr,
            text=True,
            encoding=encoding or "utf-8",
            errors=errors or "replace",
            creationflags=CREATE_NO_WINDOW,
        )
        # 尝试 Assign 进 Job；失败（已在另一 Job 或权限不足）则降级为 Popen 治理。
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001
        PROCESS_QUERY_INFORMATION = 0x0400
        handle = _kernel32.OpenProcess(
            PROCESS_TERMINATE | PROCESS_QUERY_INFORMATION, False, proc.pid
        )
        if handle:
            try:
                _AssignProcessToJobObject(self._handle, handle)
            except Exception:
                pass
            finally:
                _CloseHandle(handle)
        return proc

    def terminate(self, exit_code: int = 0) -> None:
        """用 TerminateJobObject 强制终止 Job 内所有进程。"""
        self._ensure_open()
        _TerminateJobObject(self._handle, exit_code)

    def close(self) -> None:
        """关闭 Job 句柄；若 kill_on_close，会终止其中进程树。"""
        if self._closed:
            return
        self._closed = True
        if self._handle is not None:
            _CloseHandle(self._handle)
            self._handle = None

    # --- 上下文管理器 ---

    def __enter__(self) -> "Job":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
