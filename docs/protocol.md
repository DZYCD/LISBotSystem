# TLL v2 通讯协议（LLM 的社区）

本文档讲 `tll_protocol_v2/` 这套**节点间通讯协议**——一个机器人如何委托另一个机器人做事、如何回传结果、如何上报注册与握手。这对应"LLM 的社区"：每个 bot 是一个独立 LLM 驱动的节点，通过统一模型组成委托链社区。

> 单个节点内部的 LLM 推理机制，看 [`harness_architecture.md`](./harness_architecture.md)。

## 0. 一句话概览

**TLL v2 是一个"统一委托链"模型**：任何机器人需要别的机器人做事，就用 `task_create` 网络工具发委托；本地能做的就用本地工具；复核是等工具结果那轮的 LLM；委托链是嵌套的 LLM 循环栈。所有节点通过 MQTT 总线收发 `TASK` 消息，中央节点 skaye_sv 负责注册、握手、存档。

## 1. 节点角色与拓扑

| 节点 | 角色 | LLM |
|---|---|---|
| `skaye_sv` | Skaye 族中央汇聚：注册中心、握手调度、任务存档 | 无（V2 sv_tools 统一实现） |
| `sayi_sv` | SaYi 族中央调度（主人扮演，无 LLM） | 无 |
| `sayi_996` / `skaye_996` | 调度 / 监控机器人（有 LLM，互为搭档） | DeepSeek |
| `eiar_001` / `eiar_002` | EiAr 族编程 / 事务助手（有 LLM） | DeepSeek |

分组（大屏配色）：`SaYi`=白、`Skaye`=蓝、`EiAr`=红、`SV`=监管不参与搭档。

## 2. 统一模型（设计权威）

- **委托** = `task_create` 网络工具（调别的机器人）
- **本地工具** = 本机直接用（不通过网络）
- **复核** = 等工具结果那轮 LLM
- **委托链** = 嵌套的 LLM 循环栈
- **同步阻塞** = 委托后阻塞等回传
- **TASK id 复用**：委托链沿途复用同一个 task_id，便于追踪
- **一次一个 tool_call 串行**：每轮只串行处理模型生成的 tool_calls

## 3. 线协议数据结构（core.py）

### TASK 消息核心字段
`Task` 保留旧 TLL 线协议契约，字段兼容：

```
id           任务唯一 id（委托链复用）
from_bot     发起者
current_agent 当前处理者
tlljson: {
  from_bot, command, to, params
}
trace: { trace_id, hops: [{bot, action, timestamp}] }  委托链轨迹
status       created/pending/running/success/failed/...
route        LIFO 委托栈
logs         工具调用日志
result / output / error
```

### Trace：委托链轨迹
`Trace.add_hop(bot, action)` 记录每个 hop，action 有几种语义：
- `delegate_to_<bot>`：委托给某机器人
- `return_to_<bot>`：回传给某机器人
- `continue_to_<bot>` / 其他：中间流转

大屏解析 `delegate_to_` / `return_to_` 还原完整委托链（含逐跳回传）。

## 4. MQTT 消息封装（mqtt.py）

节点订阅 `tll/<bot_id>`，发 TASK 到目标 topic。信封格式：

```json
{
  "type": "TASK",
  "target": "agent/<to>",
  "sender": "agent/<from>",
  "timestamp": "<ISO>",
  "task": { ...Task.to_dict()... }
}
```

- 发送目标默认 `tll/<target>`。
- 中文内容 `ensure_ascii=False` 编码（PowerShell 发中文会变问号，需用 Python urllib/curl）。

## 5. 网络委托机制（transport.py — V2TLLTransport.execute）

`task_create` 工具的核心实现：

```
execute(request):
  to_bot = args["to"]; command = args["command"]
  1. 校验 to_bot 在 peers 白名单，否则拒绝 [tll: denied]
  2. task_id = args.get("task_id") or current_task_id or uuid（委托链复用）
  3. 构造 Task（继承 current_trace 并 add_hop delegate_to_<to>）
  4. 加密发到 tll/<to_bot>
  5. _pending[task_id] = future，asyncio.wait_for 同步阻塞等回传
  6. 收到回传 handle_response(task_id, result) 填充 future
```

### 委托链 task_id 复用 + 回环检测
- `current_task_id` 让委托链沿途复用同一 task_id。
- `node._in_flight` 集合：`handle_new_task` 开头 `if task.id in self._in_flight: return "[tll: rejected] 委托回环"`，try/finally discard，防止委托回环。
- **被委托方检查 task_id 是否仍挂起**（pending/未交付）：挂起则拒绝返回；已交付允许重复委托。不去重。

### 自我回环保护
回传目标若是本机自己（`target == bot_id`），丢弃不回传——防止无 LLM 节点收到 chat 后无限自环（`chat → 回传给自己 → 又收到`）。

## 6. 节点收发流程（node.py + start_v2.py）

### 收消息（on_incoming）
```
收到 TASK 消息
  ├─ task_id 在本机 _pending 有等待者 → handle_response 填充（消费回传，不走 LLM）
  └─ 否则 → handle_incoming（新任务，跑 Agent）
```

### 处理新任务（handle_incoming）
```
1. 若 skaye_sv：更新该 bot 的握手时间
2. 有 LLM → node.handle_new_task(task, llm)  跑 Agent 循环
   无 LLM → handle_new_task(task, None)       直接执行本地工具
3. task.output = 结果; status = SUCCESS
4. 若 target == 本机 → 丢弃（防自环）
5. 否则 add_hop return_to_<target> → 回传
6. 补报最终结果给 skaye_sv（含 return hop + result）
```

## 7. 上报与注册（report_sender.py + sv_tools.py）

### 启动上报（LISreport）
- bot 启动时用 `task_create` 委托 skaye_sv 的 `record_lis`，把**完整注册信息**上报。
- 上报不受 peers 白名单限制（skaye_sv 是监管中心，接受所有上报）。
- 注册信息含：`bot_id/group/name/role/tools/skills(含参数)/auth_key/network/peers/partner`。
- skaye_sv 登记进 `registered_bots.json`（大屏动态读取）。

### ping 心跳（完整信息）
- skaye_sv 自动 ping 调度（interval_s=300），向注册的 bot 发 ping。
- bot 的 ping 工具返回**完整注册信息**（网络/组别/可联系机器人/搭档/自身工具含参数），不是简单的 `{"pong":true}`。
- skaye_sv 收到任何来自某 bot 的消息即更新其 `last_handshake`。
- 大屏可点击握手时间触发**定点 ping**（GET /api/ping?bot_id=X）。

## 8. 中央汇聚与存档（sv_tools.py — TaskArchiveStore）

skaye_sv 是中央信息源，所有 bot 的 TASK 流转汇聚到 `bots/skaye_sv/task_archive/`：

- `task_archive/{task_id}.json`：每个 TASK 一份（含完整 trace 流转）。
- `events/events.jsonl`：事件流。
- bot 处理完任务后 `_report_task_to_sv_async` 上报。
- **合并策略**：同一 task_id 多次上报时，`logs`（工具调用）聚合、`trace` 累积去重、`result` 只保留最新（避免网络压力）。
- 上报 task_archive 用**独立 task_id**（避免与当前任务 pending 冲突）。

## 9. 牵线搭桥（孤岛模型）

- **孤岛**：EiAr 与 SaYi 默认隔离，互不知晓（写入 yaml 时 EiAr 清空 SaYi 配置、SaYi 清空 EiAr 配置），只有"合作"才联系。
- **Skaye 族牵线**：Skaye_996 帮搭档 SaYi_996 在 EiAr 间牵线。
- **接口常驻**：`set_eiar_contacts`（SaYi 侧）、`set_sayi_contact`（EiAr 侧），权限对 Skaye 族开放。
- Skaye_996 从 skaye_sv 的 `list_eiar_robots` 拿完整工具（含参数）用于牵线。
- 牵线写入后 `_reload_peers` 热重载 bot.yaml。

## 10. 监控大屏（dashboard_v2.py）

大屏通过 HTTP 读取 skaye_sv 的注册表与存档，**动态加载机器人**（不硬编码列表）：

- `GET /api/robots`：注册机器人（含 skaye_sv 自身 + 搭档计算）
- `GET /api/tasks`：任务存档（过滤内部工具如 task_archive，只显示真实用户任务）
- `GET /api/task?task_id=X`：单个任务完整委托链
- `GET /api/ping?bot_id=X`：定点 ping
- `POST /api/send`：以 sayi_sv 名义发任务（白名单校验）

大屏画布刻画所有节点，点击任务动态绘制委托链（`delegate_to_`蓝 / `return_to_`绿 / **留档上报橙**），点击节点选对话框目标，输入框发 chat 委托。

## 11. 启动

`tll_protocol_v2/start_all.py` 一键启动：skaye_sv 先 → 6 个 bot → dashboard。日志写 `debug_logs/{name}.log`。

```
python start_all.py
```

> 每次改 Python 代码需重启系统生效；HTML 改动即时生效（服务器读文件）。
