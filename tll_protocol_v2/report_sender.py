"""LISreport 上报器：bot 启动时主动上报给 Skaye_SV 的 record_lis。

V2 统一模型下，上报 = 构造一个 record_lis 委托任务，发给 skaye_sv 的 topic。
Skaye_SV 收到后执行其 record_lis 工具 → 登记到 registered_bots.json。

注册信息用 ToolReport（工具单一来源）生成 tools/skills，再补 bot 身份/网络/组。
上报不受 peers 白名单限制（skaye_sv 是监管中心，接受所有 bot 上报）。

用法：
    await report_to_sv(node, bot_cfg)   # 发一次注册/心跳上报
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

# 确保 harness（LIS_v2 根下的独立核心）可导入
_LIS_ROOT = Path(__file__).resolve().parents[1]
if str(_LIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIS_ROOT))

from lis_harness.report import ToolReport

from .core import Task, TaskStatus, TLLjson


def build_registration_info(node, bot_cfg: Dict) -> Dict[str, Any]:
    """构建注册/心跳信息（对齐旧 build_registration_info）。"""
    # 工具上报（单一来源：bot.yaml tool_list → public 段）
    # 传 bot.yaml 路径给 ToolReport，由它自己相对解析 tool_list（含 params）
    bot_yaml_path = getattr(node.config, "bot_yaml_path", "") or ""
    report = ToolReport(bot_yaml_path, data=bot_cfg)
    built = report.build()

    # 网络信息（networks 中第一个 mqtt）
    network_info = {}
    for net in bot_cfg.get("networks", []) or []:
        if isinstance(net, dict) and net.get("network") == "mqtt":
            network_info = {
                "url": net.get("url", ""),
                "port": net.get("port", ""),
                "topic": net.get("topic", ""),
            }
            break

    # 搭档（peer）：sayi_N ↔ skaye_N 对称配对，SV 监管不参与
    my_id = node.config.bot_id
    name = my_id.split("/")[-1] if "/" in my_id else my_id
    parts = name.split("_")
    partner = ""
    if len(parts) >= 2 and parts[1].lower() != "sv" and parts[0].lower() in ("sayi", "skaye"):
        target_prefix = "skaye" if parts[0].lower() == "sayi" else "sayi"
        exact = f"agent/{target_prefix}_{parts[1]}"
        if exact in (bot_cfg.get("peers", {}) or {}):
            partner = exact

    return {
        "bot_id": my_id,
        "group": bot_cfg.get("group", ""),
        "name": bot_cfg.get("name", ""),
        "role": bot_cfg.get("role", ""),
        "tools": built.get("tools", []),
        "skills": built.get("skills", {}),
        "auth_key": node.config.auth_key,
        "network": network_info,
        "peers": list(bot_cfg.get("peers", {}).keys()),
        "partner": partner,
    }


async def report_to_sv(node, bot_cfg: Dict, skaye_sv_id: str = "agent/skaye_sv", timeout_s: float = 10.0) -> bool:
    """发一次 record_lis 上报给 skaye_sv，等待登记结果。成功返回 True。

    用 task_create 委托（走 tll.execute + pending），这样：
    1. skaye_sv 处理 record_lis 后回传，被本机 pending 拦截（不产生回环）。
    2. 本机能确认上报成功。
    """
    info = build_registration_info(node, bot_cfg)
    tll = getattr(node, "tll", None)
    if tll is None:
        return False
    from lis_harness.security.capability import ExecutionRequest
    req = ExecutionRequest(
        tool_name="task_create",
        arguments={"to": skaye_sv_id, "command": "record_lis", "params": info},
        actor=node.config.bot_id,
    )
    try:
        result = await tll.execute(req, policy=None)
        if result.ok:
            print(f"[report] {node.config.bot_id} 已上报 LISreport -> {skaye_sv_id} (ok)")
            return True
        print(f"[report] {node.config.bot_id} 上报被拒: {result.error}")
        return False
    except Exception as e:
        print(f"[report] {node.config.bot_id} 上报失败: {e}")
        return False
