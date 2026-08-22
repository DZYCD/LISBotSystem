"""牵线接口工具：EiAr/SaYi 两族的常驻注册接口（权限对 Skaye 族开放）。

- EiAr 侧 `set_sayi_contact`：清空本机所有 SaYi 配置 → 写入要合作的 SaYi 联系方式（不含工具）。
- SaYi 侧 `set_eiar_contacts`：清空本机所有 EiAr 配置 → 写入要合作的 EiAr 联系方式+工具。

调用方必须是 Skaye 族（partner 卫星），否则拒绝。
写入后触发回调（调用方 node 热重载 peers）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 确保 harness（LIS_v2 根下的独立核心）可导入
_LIS_ROOT = Path(__file__).resolve().parents[1]
if str(_LIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIS_ROOT))

from .bot_config import BotConfigManager


def create_set_sayi_contact_tool(bot_yaml_path, on_change: Optional[Callable] = None):
    """EiAr 侧的 `set_sayi_contact` 工具后端。"""
    class SetSayiBackend:
        name = "contact"
        def __init__(self, path, cb): self._path, self._cb = path, cb
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            actor = request.actor
            if not _is_skaye(actor):
                return ExecutionResult(ok=False, denied=True,
                                       error=f"set_sayi_contact: {actor} 无权（仅 Skaye 族）")
            sayi_id = request.arguments.get("sayi_id")
            sayi_info = request.arguments.get("sayi_info", {})
            if not sayi_id:
                return ExecutionResult(ok=False, error="set_sayi_contact: missing sayi_id", denied=False)
            mgr = BotConfigManager(self._path)
            result = mgr.set_sayi_contact(sayi_id, sayi_info)
            if self._cb:
                self._cb()
            return ExecutionResult(ok=True, value=result)
    return SetSayiBackend(bot_yaml_path, on_change)


def create_set_eiar_contacts_tool(bot_yaml_path, on_change: Optional[Callable] = None):
    """SaYi 侧的 `set_eiar_contacts` 工具后端。"""
    class SetEiarBackend:
        name = "contact"
        def __init__(self, path, cb): self._path, self._cb = path, cb
        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            actor = request.actor
            if not _is_skaye(actor):
                return ExecutionResult(ok=False, denied=True,
                                       error=f"set_eiar_contacts: {actor} 无权（仅 Skaye 族）")
            eiar_list = request.arguments.get("eiar_list", [])
            if not isinstance(eiar_list, list) or not eiar_list:
                return ExecutionResult(ok=False, error="set_eiar_contacts: need non-empty eiar_list", denied=False)
            mgr = BotConfigManager(self._path)
            result = mgr.set_eiar_contacts(eiar_list)
            if self._cb:
                self._cb()
            return ExecutionResult(ok=True, value=result)
    return SetEiarBackend(bot_yaml_path, on_change)


def create_contact_backend(bot_id: str, bot_yaml_path, on_change: Optional[Callable] = None):
    """按机器人族创建对应的牵线接口后端（供 node 从 yaml implements: contact 路由）。

    - EiAr → set_sayi_contact 后端
    - SaYi → set_eiar_contacts 后端
    """
    if bot_id.startswith("agent/eiar"):
        return create_set_sayi_contact_tool(bot_yaml_path, on_change)
    if bot_id.startswith("agent/sayi"):
        return create_set_eiar_contacts_tool(bot_yaml_path, on_change)
    raise ValueError(f"contact 后端不支持机器人 {bot_id}（仅 EiAr/SaYi）")


def _is_skaye(bot_id: str) -> bool:
    return bool(bot_id) and bot_id.startswith("agent/skaye")
