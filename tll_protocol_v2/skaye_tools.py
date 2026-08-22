"""Skaye 族专用工具：牵线者自权限管理。

Skaye 族（卫星）作为牵线者，需要能委托 EiAr/SaYi 两端的牵线接口。
但孤岛模型下它没有两端中任何一端的全量鉴权（peers 白名单）。

解决：Skaye 从 skaye_sv 的 list_eiar_robots 拿到 EiAr 名单后，用本模块的
private 函数把名单写入自己 bot.yaml 的 peers（含鉴权），从而获得委托
eiar_001 的 set_sayi_contact 的能力。

这些是 Skaye 的 private 内置函数（只给自己用，不上报）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .bot_config import BotConfigManager


def _is_skaye(bot_id: str) -> bool:
    return bool(bot_id) and bot_id.startswith("agent/skaye")


def create_add_eiar_peers_tool(bot_yaml_path, on_change: Optional[Callable] = None):
    """Skaye 的 private：把 EiAr 名单写入自己的 peers（含鉴权），获得委托权限。"""
    class AddEiarPeers:
        name = "skaye_perm"

        def __init__(self, path, cb):
            self._path, self._cb = path, cb

        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            actor = request.actor
            # 只允许 Skaye 族用（自己给自己加权限）
            if not _is_skaye(actor):
                return ExecutionResult(ok=False, denied=True,
                                       error=f"add_eiar_peers: {actor} 无权（仅 Skaye 族）")
            eiar_list = request.arguments.get("eiar_list", [])
            if not isinstance(eiar_list, list) or not eiar_list:
                return ExecutionResult(ok=False, error="add_eiar_peers: need non-empty eiar_list", denied=False)
            mgr = BotConfigManager(self._path)
            added = []
            for eiar in eiar_list:
                bot_id = eiar.get("bot_id")
                if not bot_id or not eiar.get("auth_key"):
                    continue
                # 写入联系方式 + 工具 + 鉴权（获得委托能力）
                mgr.add_peer(bot_id, eiar)
                added.append(bot_id)
            mgr.save()
            if self._cb:
                self._cb()
            return ExecutionResult(ok=True, value={
                "status": "success", "added": added,
                "peers": list(mgr.peers().keys()),
            })
    return AddEiarPeers(bot_yaml_path, on_change)


def create_remove_peer_tool(bot_yaml_path, on_change: Optional[Callable] = None):
    """Skaye 的 private：从自己的 peers 移除某机器人（清理无用的鉴权）。"""
    class RemovePeer:
        name = "skaye_perm"

        def __init__(self, path, cb):
            self._path, self._cb = path, cb

        async def execute(self, request, policy):
            from lis_harness.security.capability import ExecutionResult
            actor = request.actor
            if not _is_skaye(actor):
                return ExecutionResult(ok=False, denied=True,
                                       error=f"remove_peer: {actor} 无权（仅 Skaye 族）")
            bot_id = request.arguments.get("bot_id")
            if not bot_id:
                return ExecutionResult(ok=False, error="remove_peer: need bot_id", denied=False)
            mgr = BotConfigManager(self._path)
            mgr.remove_peer(bot_id)
            mgr.save()
            if self._cb:
                self._cb()
            return ExecutionResult(ok=True, value={
                "status": "success", "removed": bot_id,
                "peers": list(mgr.peers().keys()),
            })
    return RemovePeer(bot_yaml_path, on_change)
