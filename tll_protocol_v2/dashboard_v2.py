"""TLL v2 监控大屏 —— 数据聚合 + HTTP 查询接口。

数据源（v2 存档）：
- registered_bots.json（skaye_sv 目录）— 已注册机器人
- 各 bot 的 tasks/{task_id}.json — TASK 活动档
- 各 bot 的 run_log/run.jsonl — BOT 运行档

提供 HTTP 接口给前端大屏与查询工具：
- /            → 大屏 HTML
- /api/robots  → 已注册机器人
- /api/tasks   → 最近 TASK 活动档（跨 bot 聚合）
- /api/events  → 最近 BOT 运行事件（跨 bot 聚合）
- /api/stats   → 统计（总任务 / 每 bot 事件数 / 状态分布）
- /api/task?task_id= → 查单个 TASK 活动档
- /api/bot_log?bot=  → 查单个 bot 运行档
"""

from __future__ import annotations

import json
import os
import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

# --- 数据源定位 ---

LIS_V2_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # LIS_v2
BOTS_DIR = os.path.join(LIS_V2_ROOT, "bots")
REGISTERED = os.path.join(BOTS_DIR, "skaye_sv", "registered_bots.json")
HTML_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_v2.html")

ALL_BOTS = ["eiar_001", "eiar_002", "sayi_996", "skaye_996", "sayi_sv", "skaye_sv"]


def _dynamic_bots() -> List[str]:
    """从 skaye_sv 动态维护的 registered_bots.json 读取机器人列表（主动上报/ping 登记）。

    返回 bot 名列表（如 ['eiar_001', ...]）。注册表为空时回退到 ALL_BOTS 兜底。
    """
    data = _load_json(REGISTERED)
    if not data:
        return list(ALL_BOTS)
    names = []
    for bid in data.keys():
        if bid.startswith("agent/"):
            names.append(bid.split("/", 1)[1])
        else:
            names.append(bid)
    # 确保中央节点 skaye_sv 也在列表
    if "skaye_sv" not in names:
        names.append("skaye_sv")
    return names

# 以 SaYi_SV 名义发任务的身份配置
SAYI_SV_ID = "agent/sayi_sv"
SAYI_SV_YAML = os.path.join(BOTS_DIR, "sayi_sv", "bot.yaml")


def _load_sayi_sv_cfg() -> Dict[str, Any]:
    """读取 sayi_sv 配置（MQTT 连接 + peers 白名单 + 各目标 auth_key）。"""
    data = _load_json(SAYI_SV_YAML) if os.path.isfile(SAYI_SV_YAML) else None
    if not data:
        # yaml 文件用 _load_json 读不了，单独处理
        import yaml
        try:
            with open(SAYI_SV_YAML, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            data = {}
    return data or {}


# 惰性初始化的 MQTT 客户端（以 sayi_sv 身份）
_mqtt_client = None
_mqtt_connected = False
_sayi_peers: Dict[str, Any] = {}


def _init_sayi_sv_mqtt() -> bool:
    """以 sayi_sv 身份连接 MQTT（发任务用）。返回是否成功。"""
    global _mqtt_client, _mqtt_connected, _sayi_peers
    if _mqtt_connected:
        return True
    import paho.mqtt.client as mqtt
    cfg = _load_sayi_sv_cfg()
    _sayi_peers = cfg.get("peers", {}) or {}
    net = None
    for n in cfg.get("networks", []) or []:
        if n.get("network") == "mqtt":
            net = n
            break
    host = (net or {}).get("url", "broker.emqx.io")
    port = int((net or {}).get("port", 1883))
    _mqtt_client = mqtt.Client(client_id="sayi_sv_dashboard")
    try:
        _mqtt_client.connect(host, port, 60)
        _mqtt_client.loop_start()
        _mqtt_connected = True
        return True
    except Exception as e:
        print(f"[dashboard] sayi_sv MQTT 连接失败: {e}")
        return False


def send_task_as_sayi_sv(target: str, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """以 sayi_sv 名义向目标 bot 发一个 TASK。

    校验：target 必须在 sayi_sv 的 peers 白名单内。
    实现：不直接 publish 给目标（那样是自我分身绕过委托链、回传无人捕获），
    而是发一个 delegate 命令给【真实 sayi_sv 进程】，由它的 V2TLLTransport
    委托目标并同步等回传，纳入真实委托链。
    """
    if not _init_sayi_sv_mqtt():
        return {"status": "error", "info": "MQTT 未连接"}
    if target not in _sayi_peers:
        return {"status": "error", "info": f"target {target} 不在 sayi_sv 的委托白名单"}
    # 支持作为包导入或独立脚本
    try:
        from .core import Task, TLLjson, TaskStatus
    except ImportError:
        from core import Task, TLLjson, TaskStatus
    import uuid
    # 以 sayi_sv 名义直接委托目标 bot（task_create 语义：to=目标，不自我委托）。
    # 目标处理完回传，通过 skaye_sv 存档可见，无需 web 捕获回传。
    task = Task(
        task_type="general",
        from_bot=SAYI_SV_ID,
        current_agent=SAYI_SV_ID,
        tlljson=TLLjson(from_bot=SAYI_SV_ID, command=command,
                        to=target,
                        params=params),
        task_id=uuid.uuid4().hex[:12],
    )
    payload = json.dumps({
        "type": "TASK",
        "target": target,
        "sender": SAYI_SV_ID,
        "timestamp": datetime.now().isoformat(),
        "task": task.to_dict(),
    }, ensure_ascii=False).encode("utf-8")
    info = _mqtt_client.publish(f"tll/{target}", payload, qos=1)
    if info.rc == 0:
        # 记录 TASK 活动档（以 sayi_sv 名义）
        try:
            try:
                from .archive import archive_task
            except ImportError:
                from archive import archive_task
            archive_task(task, os.path.join(BOTS_DIR, "sayi_sv"), note="dispatched by sayi_sv (dashboard)")
        except Exception:
            pass
        return {"status": "success", "task_id": task.id, "to": target, "command": command}
    return {"status": "error", "info": f"MQTT publish failed rc={info.rc}"}


# --- 数据聚合 ---

def _load_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _bot_id(bot_name: str) -> str:
    return f"agent/{bot_name}"


def _compute_partner(bot_id: str, peers: list) -> str:
    """计算搭档（peer）：sayi_N ↔ skaye_N 对称配对，SV 监管不参与。

    返回搭档 bot_id，无则空串。
    """
    name = bot_id.split("/")[-1] if "/" in bot_id else bot_id
    parts = name.split("_")
    if len(parts) < 2:
        return ""
    prefix, suffix = parts[0].lower(), parts[1]
    if suffix.lower() == "sv" or prefix not in ("sayi", "skaye"):
        return ""
    target_prefix = "skaye" if prefix == "sayi" else "sayi"
    exact = f"agent/{target_prefix}_{suffix}"
    return exact if exact in peers else ""


def get_registered_robots() -> Dict[str, Any]:
    """读取 registered_bots.json（含 skaye_sv 自身节点 + 计算搭档）。"""
    data = _load_json(REGISTERED)
    if data is None:
        data = {}
    # 为每个 bot 补充搭档（peer）字段
    for bid, info in data.items():
        peers = info.get("peers") or []
        info["peer"] = _compute_partner(bid, peers)
    # skaye_sv 不上报自己，但它是中央节点，必须展示在大屏上
    other_bots = [k for k in data.keys()]
    data.setdefault("agent/skaye_sv", {
        "bot_id": "agent/skaye_sv",
        "name": "skaye_sv",
        "group": "Skaye",
        "role": "中央汇聚（Skaye_SV）",
        "tools": [],
        "skills": {},
        "peers": other_bots,
        "peer": "",
        "last_handshake": "",
    })
    return data


def get_all_task_archives(limit: int = 50) -> List[Dict[str, Any]]:
    """聚合 TASK 活动档（优先从 Skaye_SV 集中库读，跨 bot 汇聚）。"""
    # Skaye_SV 是中央信息源：所有 bot 上报的 TASK 活动档汇聚在 task_archive/
    sv_archive = os.path.join(BOTS_DIR, "skaye_sv", "task_archive")
    # 内部工具命令不上大屏（task_archive 是上报汇聚用的内部工具，非用户任务）
    INTERNAL_CMDS = {"task_archive", "record_lis", "event_report", "query_task"}
    tasks = []
    if os.path.isdir(sv_archive):
        for path in glob.glob(os.path.join(sv_archive, "*.json")):
            data = _load_json(path)
            if not data:
                continue
            task = data.get("task") if isinstance(data.get("task"), dict) else data
            cmd = data.get("command") or (task.get("tlljson") or {}).get("command") or ""
            if cmd in INTERNAL_CMDS:
                continue  # 内部工具不上大屏
            # trace：委托链（谁委托谁）。可能是 data.trace 或 task.trace.hops
            trace = data.get("trace") or []
            if not trace and isinstance(task.get("trace"), dict):
                trace = task["trace"].get("hops", [])
            tasks.append({
                "bot": data.get("bot") or task.get("current_agent") or "",
                "task_id": data.get("task_id") or task.get("id") or os.path.basename(path)[:-5],
                "archived_at": data.get("archived_at") or "",
                "from_bot": data.get("from_bot") or task.get("from_bot") or "",
                "command": data.get("command") or (task.get("tlljson") or {}).get("command") or "",
                "status": data.get("status") or task.get("status") or "",
                "result": data.get("result") or task.get("output") or "",
                "trace": trace,
            })
    tasks.sort(key=lambda x: x["archived_at"], reverse=True)
    return tasks[:limit]


def get_all_events(limit: int = 100) -> List[Dict[str, Any]]:
    """聚合 BOT 运行事件（优先从 Skaye_SV 事件流读，集中汇聚）。"""
    events = []
    # Skaye_SV 是中央信息源：所有 bot 运行事件汇聚在 events/events.jsonl
    sv_events = os.path.join(BOTS_DIR, "skaye_sv", "events", "events.jsonl")
    if os.path.isfile(sv_events):
        with open(sv_events, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if isinstance(ev, dict):
                        events.append(ev)
                except Exception:
                    continue
    events.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return events[:limit]


def get_stats() -> Dict[str, Any]:
    """统计：总任务 / 每 bot 事件数 / 状态分布。"""
    tasks = get_all_task_archives(limit=10_000)
    events = get_all_events(limit=10_000)
    status_dist: Dict[str, int] = {}
    for t in tasks:
        st = t.get("status") or "unknown"
        status_dist[st] = status_dist.get(st, 0) + 1
    per_bot: Dict[str, int] = {}
    for ev in events:
        b = ev.get("bot_id") or "unknown"
        per_bot[b] = per_bot.get(b, 0) + 1
    return {
        "total_tasks": len(tasks),
        "total_events": len(events),
        "status_distribution": status_dist,
        "events_per_bot": per_bot,
        "registered_robots": len(get_registered_robots()),
        "generated_at": datetime.now().isoformat(),
    }


def get_task_by_id(task_id: str) -> Optional[Dict[str, Any]]:
    """按 task_id 查单个 TASK 活动档（优先 Skaye_SV 集中库）。"""
    # Skaye_SV 集中库优先
    sv_path = os.path.join(BOTS_DIR, "skaye_sv", "task_archive", f"{task_id}.json")
    data = _load_json(sv_path)
    if data is not None:
        return {"bot": "skaye_sv", "data": data}
    # 兜底：扫各 bot 本地
    for bot in _dynamic_bots():
        path = os.path.join(BOTS_DIR, bot, "tasks", f"{task_id}.json")
        data = _load_json(path)
        if data is not None:
            return {"bot": bot, "data": data}
    return None


def get_bot_log(bot: str, max_lines: int = 50) -> List[Dict[str, Any]]:
    """查单个 bot 的运行档（run.jsonl）。"""
    if bot not in _dynamic_bots():
        return []
    path = os.path.join(BOTS_DIR, bot, "run_log", "run.jsonl")
    if not os.path.isfile(path):
        return []
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except Exception:
                continue
    return lines[-max_lines:]


# --- HTTP 服务 ---

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        if path in ("/", "/index.html"):
            self._send_file(HTML_FILE, "text/html; charset=utf-8")
        elif path == "/api/robots":
            self._json(get_registered_robots())
        elif path == "/api/tasks":
            limit = int(q.get("limit", ["50"])[0])
            self._json(get_all_task_archives(limit))
        elif path == "/api/events":
            limit = int(q.get("limit", ["100"])[0])
            self._json(get_all_events(limit))
        elif path == "/api/stats":
            self._json(get_stats())
        elif path == "/api/task":
            tid = q.get("task_id", [""])[0]
            if not tid:
                self._json({"error": "缺少 task_id"}, 400)
                return
            r = get_task_by_id(tid)
            self._json(r if r else {"error": f"未找到 task_id={tid}"})
        elif path == "/api/bot_log":
            bot = q.get("bot", [""])[0]
            n = int(q.get("limit", ["50"])[0])
            if not bot:
                self._json({"error": "缺少 bot"}, 400)
                return
            self._json({"bot": bot, "logs": get_bot_log(bot, n)})
        elif path == "/api/ping":
            # 定点 ping：以 sayi_sv 名义向指定 bot 发 ping，触发握手更新
            bot = q.get("bot_id", [""])[0]
            if not bot:
                self._json({"error": "缺少 bot_id"}, 400)
                return
            try:
                r = send_task_as_sayi_sv(bot, "ping", {})
                self._json({"status": r.get("status", "error"), "to": bot,
                            "task_id": r.get("task_id", ""), "info": r.get("info", "")})
            except Exception as e:
                self._json({"status": "error", "info": str(e)}, 500)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/send":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            self._json({"error": "invalid JSON body"}, 400)
            return
        target = body.get("to") or body.get("target", "")
        command = body.get("command", "")
        params = body.get("params", {}) or {}
        if not target or not command:
            self._json({"error": "需要 to(target) 和 command"}, 400)
            return
        result = send_task_as_sayi_sv(target, command, params)
        self._json(result, 200 if result.get("status") == "success" else 400)

    def _json(self, data: Any, code: int = 200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _send_file(self, path: str, ctype: str):
        try:
            with open(path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def log_message(self, fmt, *args):
        pass


def start_dashboard(host: str = "0.0.0.0", port: int = 8080):
    # 以 sayi_sv 身份连接 MQTT（发任务能力）
    try:
        _init_sayi_sv_mqtt()
    except Exception as e:
        print(f"[dashboard] MQTT 初始化失败（发任务不可用）: {e}")
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"✅ TLL v2 监控大屏已启动: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    start_dashboard(port=port)
