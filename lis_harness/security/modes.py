"""沙箱模式（SandboxMode）与范围语义。

模式刻画一次执行被允许的工作范围。这是沙箱的核心抽象：范围是分档的，
从最保守的只读到完全放开。
"""

from __future__ import annotations

from enum import Enum


class SandboxMode(Enum):
    """一次执行允许的工作范围档位。

    三个档位由保守到放开，对应 dsh 的 sandbox-mode 三档。
    """

    READ_ONLY = "read-only"
    """只读：不可写任何文件，不可产生落盘副作用。"""

    WORKSPACE_WRITE = "workspace-write"
    """工作区写：只允许在工作区（workspace root）内读写。"""

    DANGER_FULL_ACCESS = "danger-full-access"
    """完全放开：可写任意路径，仅建议用于可信操作。"""

    @property
    def allows_write(self) -> bool:
        """本档位是否允许任何形式的写副作用。"""
        return self is not SandboxMode.READ_ONLY

    def can_upgrade_to(self, other: "SandboxMode") -> bool:
        """判断一次执行能否从本档位升级到 other 档位。

        升级必须走向更宽的范围；同档或更窄不算升级。
        """
        return self._rank() < other._rank()

    def _rank(self) -> int:
        if self is SandboxMode.READ_ONLY:
            return 0
        if self is SandboxMode.WORKSPACE_WRITE:
            return 1
        return 2
