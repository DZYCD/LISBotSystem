"""bot.yaml 配置管理：牵线接口的底层读写能力。

孤岛模型：每次写入时清空某一族方向的旧配置，只保留当前合作对象。
- EiAr 侧：清空 peers 里所有 SaYi 配置，写入当前要合作的 SaYi（不含工具）。
- SaYi 侧：清空 peers 里所有 EiAr 配置，写入当前要合作的 EiAr（含工具）。
- Skaye_SV 作为注册中心永远保留（不属 EiAr/SaYi 族，不参与清空）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# 族判断
EIAR_GROUPS = {"EiAr"}
SAYI_GROUPS = {"SaYi"}


def _is_eiar(bot_id: str) -> bool:
    return bot_id.startswith("agent/eiar")


def _is_sayi(bot_id: str) -> bool:
    return bot_id.startswith("agent/sayi")


def _is_sv(bot_id: str) -> bool:
    return "sv" in bot_id.lower()


class BotConfigManager:
    """读写某机器人的 bot.yaml peers（牵线接口用）。"""

    def __init__(self, bot_yaml_path: str | os.PathLike) -> None:
        self.bot_yaml_path = Path(bot_yaml_path)
        self.data = self._load()

    def _load(self) -> Dict:
        with open(self.bot_yaml_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def save(self) -> None:
        with open(self.bot_yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=False)

    def peers(self) -> Dict[str, Dict]:
        return self.data.setdefault("peers", {})

    def add_peer(self, bot_id: str, peer_info: Dict) -> None:
        self.peers()[bot_id] = peer_info

    def remove_peer(self, bot_id: str) -> None:
        self.peers().pop(bot_id, None)

    def clear_family_peers(self, family: str) -> None:
        """清空 peers 里属于某族的机器人配置。family: 'eiar' | 'sayi'。"""
        keep = {}
        for pid, pinfo in self.peers().items():
            if family == "eiar" and _is_eiar(pid):
                continue  # 丢弃
            if family == "sayi" and _is_sayi(pid):
                continue
            keep[pid] = pinfo
        self.data["peers"] = keep

    # --- 具体操作 ---

    def set_sayi_contact(self, sayi_id: str, sayi_info: Dict) -> Dict:
        """EiAr 侧：清空所有 SaYi 配置，写入当前 SaYi（不含工具）。"""
        self.clear_family_peers("sayi")
        # 只保留联系方式（不含 tools）
        contact = {k: v for k, v in sayi_info.items() if k != "tools"}
        self.add_peer(sayi_id, contact)
        self.save()
        return {"status": "success", "sayi": sayi_id, "peers": list(self.peers().keys())}

    def set_eiar_contacts(self, eiar_list: List[Dict]) -> Dict:
        """SaYi 侧：清空所有 EiAr 配置，写入当前 EiAr 列表（含工具）。"""
        self.clear_family_peers("eiar")
        for eiar in eiar_list:
            bot_id = eiar.get("bot_id")
            if not bot_id:
                continue
            # 写入联系方式 + 工具
            self.add_peer(bot_id, eiar)
        self.save()
        return {"status": "success", "eiars": [e.get("bot_id") for e in eiar_list],
                "peers": list(self.peers().keys())}
