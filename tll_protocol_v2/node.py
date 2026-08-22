"""TLL v2 机器人节点 —— 装配 harness Agent，处理 TASK 收发与回传。

核心职责：
1. 从 bot.yaml 装配：peers（委托白名单）、auth_key、skills（本地工具）。
2. 构造 harness 组件：Registry / 执行管线 / ToolRuntime / V2TLLTransport / Agent。
3. 注册本地工具（从 skills/ 扫描）+ task_create（网络委托）。
4. MQTT 收消息：
   - 回传（task_id 有等待者）→ handle_response 填充 future（唤醒阻塞 Agent）
   - 新任务 → 跑 harness Agent 循环 → 结果作为回传发回
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# 确保 harness（LIS_v2 根下的独立核心）可导入
_LIS_ROOT = Path(__file__).resolve().parents[1]
if str(_LIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIS_ROOT))

from lis_harness.agent import Agent
from lis_harness.llm import LlmClient
from lis_harness.registry import Registry, ToolDefinition, ToolRuntime
from lis_harness.security import (
    ApprovalOutcome,
    CallbackApprovalService,
    ExecutionPipeline,
    SandboxMode,
    SandboxPolicyResolver,
)
from lis_harness.skill_loader import SkillLoader

from .core import Task, TaskStatus, TLLjson
from .mqtt import MQTTConfig, MQTTTransport
from .transport import V2TLLTransport


@dataclass
class NodeConfig:
    bot_id: str = "agent/eiar_001"
    auth_key: str = ""
    peers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_topic: str = "tll/agent/eiar_001"
    skills_dir: str = ""            # skills 目录（扫描本地工具）
    llm_provider: str = "mock"      # mock | deepseek
    tll_backend: Any = None         # 注入 TLL 能力后端（测试用；None 则用真实 V2TLLTransport）
    system_prompt: str = ""         # 注入 Agent 的系统提示（默认 harness 默认）
    system_layers: List[str] = field(default_factory=list)  # 分层提示词（稳定在前，缓存优化）
    tool_list_path: str = ""        # 工具清单 yaml 路径（相对 bot.yaml；从 public 段注册本地工具）
    workspace_dir: str = ""         # 本地文件工具的基目录（默认 bot.yaml 所在目录）
    bot_yaml_path: str = ""         # bot.yaml 绝对路径（牵线接口写 peers 用）
    memory_enabled: bool = False    # 启用长期记忆（会话压缩 → 知识库沉淀 + 记忆工具）
    memory_window_tokens: int = 200_000  # 记忆上下文窗口（触发压缩的 token 阈值）


class Node:
    """一个 TLL v2 机器人节点。"""

    def __init__(self, config: NodeConfig) -> None:
        self.config = config
        self.registry = Registry()
        self._bus = None
        self._memory = None
        self._backends = set()  # 已注册的后端名（防重复）
        # 持久会话：按对话方维护（from_bot -> Session），跨消息记忆。
        # 委托只是把 TASK 传来传去，各机器人负责自己那一侧的对话历史。
        self._sessions = {}
        # 本机正在处理（挂起）的 task_id 集合。用于检测委托回环：
        # 若同一 task_id 又回到本机且仍在挂起，说明形成了委托环（A→B→C→A），拒绝。
        # 若该 task_id 已交付（不挂起），允许重复委托。
        self._in_flight = set()
        self._build_pipeline()
        self._build_transport()
        self._build_tools()
        self._init_memory()
        self._agent = None  # 每个任务新建 Agent（但复用持久 session）

    # --- 长期记忆 ---

    def _init_memory(self) -> None:
        """若启用，创建 MemoryManager 并注册 memory_query 工具。"""
        if not self.config.memory_enabled:
            return
        from .memory import MemoryManager
        self._memory = MemoryManager(
            bot_id=self.config.bot_id,
            base_dir=self.config.workspace_dir or os.getcwd(),
            window_tokens=self.config.memory_window_tokens,
        )
        # 注册 memory_query 本地工具（供 LLM 自主查询知识库）
        self._register_memory_tools()

    def _register_memory_tools(self) -> None:
        class MemoryBackend:
            name = "memory"
            def __init__(self, mem): self._mem = mem
            async def execute(self, request, policy):
                from lis_harness.security.capability import ExecutionResult
                text = request.arguments.get("text", "")
                items = self._mem.query(text, top_k=5)
                if not items:
                    return ExecutionResult(ok=True, value={"found": False, "note": "知识库中无相关记忆"})
                return ExecutionResult(ok=True, value={"found": True, "items": items})
        backend = MemoryBackend(self._memory)
        self.registry.register_backend("memory", backend)
        self.registry.register_tool(ToolDefinition(
            name="memory_query",
            description="查询长期记忆知识库，根据关键词返回相关知识点（跨会话记忆）",
            parameters={"type": "object", "properties": {
                "text": {"type": "string", "description": "要查询的内容/关键词"},
            }, "required": ["text"]},
            backend="memory",
        ))

    def _build_pipeline(self) -> None:
        approval = CallbackApprovalService(
            lambda request, reason: ApprovalOutcome.ALLOWED_ONCE)
        resolver = SandboxPolicyResolver(
            default_mode=SandboxMode.DANGER_FULL_ACCESS,
            workspace_root=None,
        )
        self.pipeline = ExecutionPipeline(policy_resolver=resolver, approval=approval)
        self.tool_runtime = ToolRuntime(self.registry, self.pipeline)

    def _build_transport(self) -> None:
        self.mqtt = MQTTTransport(MQTTConfig(
            host=self.config.mqtt_host,
            port=self.config.mqtt_port,
            topic=self.config.mqtt_topic,
            # client_id 用 bot 名（去 agent/ 前缀），避免 broker 对完整 client_id
            # （含斜杠）的 ACL 拒绝（实测 broker 拒 rc=5 for "agent/sayi_sv"）
            client_id=self.config.bot_id.split("/")[-1],
            auth_key=self.config.auth_key,
        ))
        if self.config.tll_backend is not None:
            # 注入的后端（测试用）
            self.tll = self.config.tll_backend
        else:
            from lis_harness.adapters import TLLTransportConfig
            peers_cfg = {pid: {"tools": (p.get("tools") if isinstance(p, dict) else [])}
                         for pid, p in self.config.peers.items()}
            self.tll = V2TLLTransport(
                TLLTransportConfig(my_bot_id=self.config.bot_id, peers=peers_cfg),
                self.mqtt, self.config.bot_id,
            )
        self.register_tll_backend(self.tll)

        # 注册 task_create 工具（网络委托）——to 参数约束为 peers 白名单
        from lis_harness.tools.task_create_tool import create as make_task_create
        tc = make_task_create({})
        peer_ids = list(self.config.peers.keys())
        # 把 to 参数 schema 限定为可委托的白名单机器人（LLM 不会选错目标）
        if peer_ids:
            props = tc.parameters.setdefault("properties", {})
            if "to" in props:
                props["to"] = {
                    "type": "string",
                    "enum": peer_ids,
                    "description": "目标机器人，只能从可委托白名单中选择: " + ", ".join(peer_ids),
                }
        self.registry.register_tool(tc)

        # MQTT 回传桥接：收到回传 → 填充 future
        self.mqtt.on_return = self._handle_return

    def register_tll_backend(self, backend) -> None:
        """注册 TLL 能力后端（默认为 V2TLLTransport，测试可注入模拟）。"""
        self.registry.register_backend("tll", backend)

    def _build_tools(self) -> None:
        """注册本地工具。

        优先从工具清单 yaml（tool_list_path，public 段）注册本地文件工具；
        否则 fallback 从 skills/ 目录扫描。
        """
        if self.config.tool_list_path and os.path.isfile(self.config.tool_list_path):
            self._register_tools_from_manifest()
            return
        if self.config.skills_dir and os.path.isdir(self.config.skills_dir):
            loader = SkillLoader(Path(self.config.skills_dir))
            loader.load_into(self.registry)

    def _register_tools_from_manifest(self) -> None:
        """从工具清单 yaml 的 public + private 段注册本地工具（按 implements 路由后端）。

        public 段：对网络开放（上报），也可自己调用。
        private 段：只给自己用（不上报），如 Skaye 的自权限管理函数。

        implements 取值：
        - local → _LocalFileBackend（文件操作）
        - contact → contact_tools（牵线接口，Skaye 族可调）
        - skaye_perm → skaye_tools（Skaye 自权限：把 EiAr 名单写入自己 peers）
        - skill:<name> → 从 skills/ 加载 <name> 的 tool.py handle
        """
        with open(self.config.tool_list_path, encoding="utf-8") as f:
            manifest = yaml.safe_load(f) or {}
        public = manifest.get("public", {}) or {}
        private = manifest.get("private", {}) or {}
        if not public and not private:
            return
        # 按 implements 分组后端实例
        local = _LocalFileBackend(self.config.workspace_dir or os.getcwd())
        contact = None
        skaye_perm = None
        skill_loader = None
        for name, meta in {**public, **private}.items():
            # 强加载工具（ping/LISreport）由运行时统一注册（返回完整注册信息），
            # 不在工具清单里重复注册，避免被 _LocalFileBackend 兜底覆盖成 "local"。
            if name in ("ping", "LISreport"):
                continue
            impl = meta.get("implements", "local")
            params = meta.get("params", {})
            if impl == "contact":
                if contact is None:
                    from .contact_tools import create_contact_backend
                    contact = create_contact_backend(self.config.bot_id, self.config.bot_yaml_path,
                                                     on_change=self._reload_peers)
                    self.registry.register_backend("contact", contact)
                backend = "contact"
            elif impl == "skaye_perm":
                if skaye_perm is None:
                    from .skaye_tools import create_add_eiar_peers_tool, create_remove_peer_tool
                    skaye_perm = {
                        "add_eiar_peers": create_add_eiar_peers_tool(self.config.bot_yaml_path,
                                                                      on_change=self._reload_peers),
                        "remove_peer": create_remove_peer_tool(self.config.bot_yaml_path,
                                                               on_change=self._reload_peers),
                    }
                    self.registry.register_backend("skaye_perm", skaye_perm["add_eiar_peers"])
                    self.registry.register_backend("skaye_perm_rm", skaye_perm["remove_peer"])
                # 工具名到后端实例映射
                backend = "skaye_perm" if name == "add_eiar_peers" else "skaye_perm_rm"
            elif impl.startswith("skill:"):
                skill_name = impl.split(":", 1)[1]
                if skill_loader is None:
                    skill_loader = SkillLoader(Path(self.config.skills_dir))
                skill_loader.load_one(self.registry, skill_name)
                continue  # load_one 已注册工具（用 skill 的 description/schema）
            elif impl == "code":
                # 沙箱代码执行后端：包装 JobObjectShell（资源治理 + 路径范围），
                # 支持 command 或 code（Python 代码自动转 python -c）
                if "code" not in self._backends:
                    from lis_harness.security.backends.jobshell import JobObjectShell
                    self.registry.register_backend("code", _CodeSandboxBackend(JobObjectShell()))
                    self._backends.add("code")
                backend = "code"
            else:
                if "local" not in self._backends:
                    self.registry.register_backend("local", local)
                    self._backends.add("local")
                backend = "local"
            self.registry.register_tool(ToolDefinition(
                name=name,
                description=meta.get("description", ""),
                parameters=_params_to_schema(params),
                backend=backend,
            ))

    # --- 回传桥接 ---

    def _handle_return(self, task_id: str, task_dict: Dict) -> bool:
        """处理回传：仅当本机确实在等该 task_id 时才消费（线程安全填充 future）。

        返回 True = 已作为回传消费；False = 本机没在等，走 on_envelope 处理新任务。
        """
        return self.tll.handle_response(task_id, task_dict)

    # --- 运行 ---

    def _build_agent(self, llm: LlmClient, session=None, bus=None) -> Agent:
        from lis_harness.agent import AgentOptions
        from lis_harness.session import Session
        layers = list(self.config.system_layers) if self.config.system_layers else None
        # 若启用记忆，把检索到的长期记忆注入 system（跨会话记忆，稳定前缀）
        if layers is not None and self._memory is not None:
            recall = self._memory.recall(self.config.bot_id, top_k=3)
            if recall:
                layers = layers + [recall]
        options = None
        if layers:
            options = AgentOptions(system_layers=layers)
        elif self.config.system_prompt:
            options = AgentOptions(system_prompt=self.config.system_prompt)
        return Agent(llm, self.tool_runtime, options=options, session=session or Session(),
                     bus=bus, bot_id=self.config.bot_id)

    def _session_for(self, from_bot: str):
        """获取/创建某个对话方的持久会话（各机器人各记各的账）。"""
        key = from_bot or "unknown"
        if key not in self._sessions:
            from lis_harness.session import Session
            self._sessions[key] = Session()
        return self._sessions[key]

    async def handle_new_task(self, task: Task, llm: LlmClient) -> Any:
        """处理一个新委托任务。

        若 task 指定了明确的本地工具命令（command 是已注册工具），直接通过
        工具运行时执行（不走 LLM）——委托方已指明要调哪个工具。
        否则跑 harness Agent 循环（LLM 决定）。用按对话方的持久会话（跨消息记忆）。

        回环检测：若该 task.id 本机正在处理（挂起），说明同一条委托链又回到本机
        （A→B→C→A 回环），返回 TLL 拒绝信息，不重复执行。若已交付（不挂起）则放行。
        """
        # 回环检测：task_id 仍挂起则拒绝
        if task.id in self._in_flight:
            print(f"[node] {self.config.bot_id} 拒绝回环: task_id={task.id} 仍在委托链挂起")
            return "[tll: rejected] 委托回环: 该 task_id 仍在委托链中处理，拒绝重复委托"
        self._in_flight.add(task.id)
        try:
            # 记录当前委托链 task_id：委托出去时复用，保持链连贯（回环检测依赖）
            prev_tid = getattr(self.tll, "current_task_id", "")
            prev_trace = getattr(self.tll, "current_trace", None)
            if hasattr(self.tll, "current_task_id"):
                self.tll.current_task_id = task.id
            # 携带委托链 trace：继承收到的 task.trace，并记录"本机收到自 from_bot"
            if hasattr(self.tll, "current_trace"):
                self.tll.current_trace = getattr(task, "trace", None)
                if self.tll.current_trace is not None and task.from_bot:
                    self.tll.current_trace.add_hop(task.from_bot, f"delegate_to_{self.config.bot_id}")
            try:
                return await self._handle_task_inner(task, llm)
            finally:
                if hasattr(self.tll, "current_task_id"):
                    self.tll.current_task_id = prev_tid
                if hasattr(self.tll, "current_trace"):
                    self.tll.current_trace = prev_trace
        finally:
            self._in_flight.discard(task.id)

    async def _handle_task_inner(self, task: Task, llm: LlmClient) -> Any:
        """handle_new_task 的实际处理体（被回环检测包裹）。"""
        from . import archive
        cmd = (task.tlljson.command if task.tlljson else "") or ""
        # BOT 运行档：记录收到一个任务
        archive.append_bot_log(self.config.workspace_dir, self.config.bot_id, {
            "event": "task.received",
            "task_id": task.id,
            "from_bot": task.from_bot,
            "command": cmd,
        })
        if self._memory is not None:
            self._memory.llm = llm
        try:
            return await self._handle_task_exec(task, llm)
        finally:
            # TASK 活动档：记录本任务在本机的流转结果
            archive.archive_task(task, self.config.workspace_dir,
                                 note=f"processed by {self.config.bot_id}")
            # 集中汇聚：把 TASK 活动档上报给 Skaye_SV（后台触发，不阻塞主流程）
            self._report_task_to_sv_async(task)

    def _report_task_to_sv_async(self, task: Task) -> None:
        """后台把 TASK 活动档委托上报给 skaye_sv（Skaye_SV 中央汇聚）。"""
        sv_id = "agent/skaye_sv"
        # 本机就是 skaye_sv，或 peers 无 skaye_sv，则跳过
        if self.config.bot_id == sv_id or sv_id not in (self.config.peers or {}):
            return
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            loop.create_task(self._do_report_task_to_sv(task, sv_id))
        except Exception:
            pass

    async def _do_report_task_to_sv(self, task: Task, sv_id: str) -> None:
        """实际委托上报（tll.execute + task_archive）。"""
        from lis_harness.security.capability import ExecutionRequest
        cmd = getattr(getattr(task, "tlljson", None), "command", "") or ""
        payload = {
            "task_id": task.id,
            "from_bot": task.from_bot,
            "bot": self.config.bot_id,
            "command": cmd,
            "status": getattr(task.status, "value", str(getattr(task.status, "", ""))),
            "result": getattr(task, "output", None),
            "archived_at": __import__("datetime").datetime.now().isoformat(),
            "trace": [h.to_dict() for h in getattr(task, "trace", []).hops] if getattr(task, "trace", None) else [],
            "logs": list(getattr(task, "logs", []) or []),
        }
        try:
            import uuid
            # 上报用独立 task_id，避免复用当前任务的 task_id 造成 pending 冲突
            # （current_task_id 复用的是委托链回环检测；上报 task_archive 需独立 id）
            req = ExecutionRequest(
                tool_name="task_create",
                arguments={"to": sv_id, "command": "task_archive", "params": payload,
                           "task_id": uuid.uuid4().hex[:12]},
                actor=self.config.bot_id,
            )
            await self.tll.execute(req, policy=None)
        except Exception as e:
            print(f"[node] {self.config.bot_id} 上报 TASK 到 {sv_id} 失败: {e}")

    async def _handle_task_exec(self, task: Task, llm: LlmClient) -> Any:
        """任务的实际执行体（BOT 运行档 + TASK 活动档已在外层记录）。"""
        # 初始化工具调用日志容器
        if not isinstance(getattr(task, "logs", None), list):
            task.logs = []
        if task.tlljson and task.tlljson.command:
            cmd = task.tlljson.command
            # delegate 命令：真实 sayi_sv 收到的"请委托"请求。
            # 由 web 大屏（自我分身）发给真实 sayi_sv 进程，这里用本机的
            # V2TLLTransport 委托目标并同步等回传——走真实委托链，pending/trace 正确。
            if cmd == "delegate":
                return await self._handle_delegate(task)
            # 排除内置 chat / task_create（这些走 LLM）
            if cmd not in ("chat", "task_create") and self._has_tool(cmd):
                # actor = 委托发起者（task.from_bot），用于工具内的权限判断
                result = await self._run_local_tool(cmd, task.tlljson.params or {}, actor=task.from_bot)
                task.logs.append({"tool": cmd, "params": task.tlljson.params, "result": result})
                return result
        # 用持久会话（按 from_bot 区分，跨消息记忆上下文）
        session = self._session_for(task.from_bot)
        # 捕获 Agent 的工具调用日志
        log_bus = _ToolLogBus(task.logs)
        agent = self._build_agent(llm, session=session, bus=log_bus)
        text = task.tlljson.params.get("text") if task.tlljson else ""
        result = await agent.run(text or "")
        # 记忆压缩检查（绑定本次会话）
        if self._memory is not None:
            self._memory.session = agent.session
            if self._memory.should_compress():
                try:
                    points = await self._memory.compress()
                    if points:
                        print(f"[memory] {self.config.bot_id} 压缩会话，沉淀 {len(points)} 条知识")
                except Exception as e:
                    print(f"[memory] compress failed: {e}")
        return result.final_text

    async def _handle_delegate(self, task: Task) -> Any:
        """处理 delegate 命令：用本机 V2TLLTransport 委托目标并同步等回传。

        web 大屏（sayi_sv_dashboard 自我分身）把 {to,command,params} 发给真实
        sayi_sv 进程，真实进程在这里走 V2TLLTransport.execute（建 pending、等
        回传、trace 正确），从而把委托纳入真实委托链，避免分身绕过导致回传丢失。
        """
        import json as _json
        params = (task.tlljson.params if task.tlljson else {}) or {}
        to = params.get("to", "")
        command = params.get("command", "")
        sub_params = params.get("params") or {}
        if not to or not command:
            return "[error] delegate requires to and command"
        try:
            from lis_harness.security.capability import ExecutionRequest
            req = ExecutionRequest(
                tool_name="task_create",
                arguments={"to": to, "command": command, "params": sub_params},
                actor=self.config.bot_id,
            )
            result = await self.tll.execute(req, policy=None)
            print(f"[delegate] {self.config.bot_id} 委托 {to}.{command} "
                  f"task_id={getattr(self.tll,'current_task_id','?')} ok={result.ok} err={getattr(result,'error','')}", flush=True)
            if result.ok:
                return _json.dumps(result.value, ensure_ascii=False)
            return f"[error] {result.error}"
        except Exception as e:
            return f"[error] delegate failed: {e}"

    def _has_tool(self, name: str) -> bool:
        try:
            self.registry.get_tool(name)
            return True
        except Exception:
            return False

    async def _run_local_tool(self, name: str, params: dict, actor: Optional[str] = None) -> Any:
        """直接执行一个已注册的本地工具，返回结果文本。

        actor 默认用本机 bot_id；网络委托来的工具用 task.from_bot（发起者），
        以便工具内做权限判断（如接触接口仅 Skaye 族可调）。
        """
        from lis_harness.registry import ToolCall
        import json as _json
        tc = ToolCall(name=name, arguments=params, actor=actor or self.config.bot_id)
        try:
            result = await self.tool_runtime.execute(tc)
            if result.ok:
                return _json.dumps(result.value, ensure_ascii=False)
            return f"[error] {result.error}"
        except Exception as e:
            return f"[error] {e}"

    def _reload_peers(self) -> None:
        """牵线接口写入后热重载：刷新 tll peers 白名单 + task_create 的 to enum。

        从 bot.yaml 重新读 peers（牵线接口写的是文件），更新 node 内存配置，
        让新加入的联系人（SaYi/EiAr）立即可被本机委托。
        """
        try:
            import yaml as _yaml
            with open(self.config.bot_yaml_path, encoding="utf-8") as _f:
                _data = _yaml.safe_load(_f) or {}
            new_peers = _data.get("peers", {}) or {}
            # 更新 node 内存配置
            self.config.peers = new_peers
            # 更新 tll 白名单
            peers_cfg = {pid: {"tools": (p.get("tools") if isinstance(p, dict) else [])}
                         for pid, p in new_peers.items()}
            self.tll._config.peers = peers_cfg
            peer_ids = list(new_peers.keys())
            for t in self.registry.list_tools():
                if t.name == "task_create":
                    props = t.parameters.setdefault("properties", {})
                    if "to" in props:
                        props["to"] = {"type": "string", "enum": peer_ids,
                                       "description": "可委托白名单: " + ", ".join(peer_ids)}
                    break
            print(f"[node] {self.config.bot_id} peers 已热重载: {peer_ids}")
        except Exception as e:
            print(f"[node] 热重载失败: {e}")

    async def serve(self, llm: LlmClient) -> None:
        """启动节点：连接 MQTT，等待消息，处理新任务并回传。"""
        self.mqtt.on_envelope = lambda data, topic: self._dispatch_new(data, llm)
        if not self.mqtt.connect():
            raise RuntimeError(f"MQTT connect failed: {self.mqtt.config.host}")

        print(f"[node] {self.config.bot_id} ready on {self.mqtt.config.topic}")
        # 阻塞主循环（真实系统由 asyncio 事件循环驱动）
        await asyncio.Event().wait()

    def _dispatch_new(self, data: Dict, llm: LlmClient) -> None:
        """收到新任务（非回传）→ 在事件循环里跑 Agent，然后回传。"""
        try:
            task = Task.from_dict(data["task"])
        except Exception as e:
            return

        def _run():
            asyncio.create_task(self._process_new(task, llm))

        # 从 MQTT 线程调度到主事件循环
        asyncio.get_event_loop().call_soon_threadsafe(_run)

    async def _process_new(self, task: Task, llm: LlmClient) -> None:
        """跑 Agent 并回传结果给 from_bot。"""
        final = await self.handle_new_task(task, llm)
        # 构造回传任务
        task.output = final
        task.result = final
        task.status = TaskStatus.SUCCESS
        target = task.from_bot or task.prev_hop
        if target:
            self.mqtt.send_task(task, target, f"tll/{target}")
        print(f"[node] {self.config.bot_id} -> {target}: {final[:60]}")


def build_node_from_yaml(bot_yaml_path: str | os.PathLike, **overrides) -> Node:
    """从 bot.yaml 自动构造 Node（所有配置从配置文件读取）。

    读取字段：id、auth_key、peers、networks[0]（mqtt）、system_layers、
    tool_list、llm（是否启用）。overrides 可覆盖个别字段（如测试注入 backend）。
    """
    bot_yaml_path = Path(bot_yaml_path)
    data = yaml.safe_load(open(bot_yaml_path, encoding="utf-8")) or {}
    net = None
    for n in data.get("networks", []) or []:
        if n.get("network") == "mqtt":
            net = n
            break
    # 解析 tool_list 相对路径
    tool_list = data.get("tool_list", "")
    tool_list_path = ""
    if tool_list:
        tl = Path(tool_list)
        if not tl.is_absolute():
            tl = bot_yaml_path.parent / tl
        tool_list_path = str(tl)
    config = NodeConfig(
        bot_id=overrides.pop("bot_id", data.get("id", "")),
        auth_key=overrides.pop("auth_key", data.get("auth_key", "")),
        peers=overrides.pop("peers", data.get("peers", {}) or {}),
        mqtt_host=overrides.pop("mqtt_host", (net or {}).get("url", "127.0.0.1")),
        mqtt_port=overrides.pop("mqtt_port", int((net or {}).get("port", 1883))),
        mqtt_topic=overrides.pop("mqtt_topic", (net or {}).get("topic", f"tll/{data.get('id','')}")),
        system_layers=overrides.pop("system_layers", list(data.get("system_layers", []) or [])),
        system_prompt=overrides.pop("system_prompt", data.get("system_prompt", "")),
        skills_dir=overrides.pop("skills_dir", str(bot_yaml_path.parent / "skills")),
        tool_list_path=overrides.pop("tool_list_path", tool_list_path),
        workspace_dir=overrides.pop("workspace_dir", str(bot_yaml_path.parent)),
        bot_yaml_path=overrides.pop("bot_yaml_path", str(bot_yaml_path)),
        memory_enabled=overrides.pop("memory_enabled", bool(data.get("memory", {}).get("enabled", False))),
        memory_window_tokens=overrides.pop("memory_window_tokens", int(data.get("memory", {}).get("window_tokens", 200_000))),
        **overrides,
    )
    return Node(config)


class _LocalFileBackend:
    """本地文件工具后端：根据工具名映射到文件操作。"""

    name = "local"

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir

    async def execute(self, request, policy):
        from lis_harness.security.capability import ExecutionResult
        name = request.tool_name
        args = request.arguments
        try:
            if name == "file_read":
                return self._read(args)
            if name == "file_write":
                return self._write(args)
            if name == "file_tree":
                return self._tree(args)
            if name in ("ping", "LISreport"):
                return ExecutionResult(ok=True, value={"pong": True, "bot": "local"})
            return ExecutionResult(ok=False, error=f"local tool {name} not implemented", denied=False)
        except Exception as e:
            return ExecutionResult(ok=False, error=f"{name} failed: {e}", denied=False)

    def _resolve(self, path: str) -> str:
        p = path
        if not os.path.isabs(p):
            p = os.path.join(self.base_dir, p)
        return p

    def _read(self, args):
        from lis_harness.security.capability import ExecutionResult
        path = self._resolve(args.get("path", ""))
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return ExecutionResult(ok=True, value={"path": args.get("path", ""), "content": content})

    def _write(self, args):
        from lis_harness.security.capability import ExecutionResult
        path = self._resolve(args.get("path", ""))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(args.get("content", ""))
        return ExecutionResult(ok=True, value={"path": args.get("path", ""), "effect": "write"})

    def _tree(self, args):
        from lis_harness.security.capability import ExecutionResult
        path = self._resolve(args.get("path", "."))
        names = sorted(os.listdir(path))
        return ExecutionResult(ok=True, value={"path": args.get("path", "."), "entries": names})


def _params_to_schema(params: dict) -> dict:
    """把工具清单 yaml 的 params 声明转成 JSON schema。"""
    properties = {}
    required = []
    for name, pinfo in (params or {}).items():
        if isinstance(pinfo, dict):
            ptype = pinfo.get("type", "string")
            properties[name] = {
                "type": ptype,
                "description": pinfo.get("description", ""),
            }
            if pinfo.get("required"):
                required.append(name)
        else:
            properties[name] = {"type": "string", "description": ""}
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


class _CodeSandboxBackend:
    """沙箱代码执行后端：包装 JobObjectShell，把 code（Python 代码）转成 command 执行。

    - command: 直接透传给底层 shell 沙箱
    - code:    自动包装成 `python -c <code>` 交给沙箱运行
    返回 stdout/stderr/exit_code，模型可看到输出与错误。
    """

    name = "code"

    def __init__(self, shell) -> None:
        self._shell = shell

    async def execute(self, request, policy):
        from lis_harness.security.capability import ExecutionResult
        args = request.arguments or {}
        code = args.get("code")
        command = args.get("command")
        if code:
            # 把 Python 代码写入临时文件，用 `python <file>` 执行，避免引号冲突
            import tempfile, os as _os
            fd, tmp = tempfile.mkstemp(suffix=".py")
            try:
                with _os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(code)
                args = dict(args)
                args["command"] = f"python {tmp}"
            finally:
                # 命令执行完删除临时文件（JobObjectShell 同步跑完才返回）
                pass
        try:
            return await self._shell.execute(request.__class__(
                tool_name=request.tool_name, arguments=args, actor=request.actor,
            ), policy)
        finally:
            # 清理临时文件（仅当用了 code）
            if code and "tmp" in locals():
                try:
                    _os.remove(tmp)
                except Exception:
                    pass


class _ToolLogBus:
    """轻量事件总线：捕获 Agent 的工具调用/结果，写入 task.logs（工具调用日志）。"""

    def __init__(self, logs: list) -> None:
        self._logs = logs

    def emit(self, event: str, data: dict) -> None:
        try:
            if event == "tool/call":
                self._logs.append({"event": "tool.call",
                                   "tool": data.get("name", ""),
                                   "args": data.get("arguments", "")})
            elif event == "tool/result":
                self._logs.append({"event": "tool.result",
                                   "tool": data.get("name", ""),
                                   "result": data.get("content", "")})
        except Exception:
            pass
