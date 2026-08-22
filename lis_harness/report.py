"""上报器：从 bot.yaml 的 tool_list 生成「可对网络开放工具」清单。

这是工具单一来源的落地：LISreport/ping 上报时，从 bot.yaml 读 tool_list
指向的工具清单 yaml，取其 public 段，运行时合并强加载的 ping/LISreport，
生成与 build_registration_info 一致的 tools/skills 结构上报。

职责：
- 读 bot.yaml → 取 tool_list 路径（相对 bot.yaml 所在目录）
- 解析工具清单 yaml → 取 public 段
- 运行时合并 ping / LISreport（不物理改写 yaml）
- 生成 {tools: [...], skills: {...}}（对齐 build_registration_info）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class ToolReport:
    """从 bot.yaml 生成工具上报清单。

    Args:
        bot_yaml_path: bot.yaml 的路径（用于解析 tool_list 相对路径）。
        data: 已解析的 bot.yaml dict；为 None 时从 bot_yaml_path 读取。
    """

    def __init__(self, bot_yaml_path: str | os.PathLike, data: Optional[Dict] = None) -> None:
        self.bot_yaml_path = Path(bot_yaml_path)
        self.data = data if data is not None else self._read_bot_yaml()

    def _read_bot_yaml(self) -> Dict:
        with open(self.bot_yaml_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def tool_list_path(self) -> Optional[Path]:
        """返回工具清单 yaml 的绝对路径（相对 bot.yaml 解析）。"""
        tool_list = self.data.get("tool_list")
        if not tool_list:
            return None
        p = Path(tool_list)
        if not p.is_absolute():
            p = self.bot_yaml_path.parent / p
        return p

    def load_tool_manifest(self) -> Dict:
        """解析工具清单 yaml，返回 {public, private} 两个段。"""
        path = self.tool_list_path()
        if path is None or not path.is_file():
            return {"public": {}, "private": {}}
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def public_tools(self) -> Dict[str, Dict]:
        """返回上报用的工具清单（public 段 + 运行时合并强加载工具）。

        强加载工具（ping/LISreport）保证在上报里，无论用户是否声明。
        """
        manifest = self.load_tool_manifest()
        public = dict(manifest.get("public", {}) or {})
        # 运行时合并强加载工具（不改文件）
        forced = {
            "ping": {
                "description": "心跳检测，返回机器人在线状态与可用工具",
                "params": {},
                "access": {"allow": ["*"]},
            },
            "LISreport": {
                "description": "上报本机开放的工具与各项数据",
                "params": {},
                "access": {"allow": ["*"]},
            },
        }
        for name, meta in forced.items():
            if name not in public:
                public[name] = meta
        return public

    def build(self) -> Dict[str, Any]:
        """构建上报结构（对齐 build_registration_info 的 tools/skills 部分）。

        Returns:
            {
                "tools": [name, ...],
                "skills": {name: {"name", "description", "access", "params"}}
            }
        """
        public = self.public_tools()
        tools = sorted(public.keys())
        skills = {}
        for name, meta in public.items():
            skills[name] = {
                "name": name,
                "description": meta.get("description", ""),
                "access": meta.get("access", {}),
                "params": meta.get("params", {}),
            }
        return {"tools": tools, "skills": skills}
