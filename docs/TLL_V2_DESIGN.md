# 新 TLL（tll_protocol_v2）架构设计草案

> 状态：设计中（待两个研究子任务报告补充契约后定稿）
> 目标：基于统一模型整体重写 TLL，让 harness Agent 循环成为执行核心。

## 一、统一模型（重写的指导原则）

```
所有网络活动都是委托链；网络委托 = 调用网络工具（工具名 + 参数字典）；
自己用自己工具 = 本地请求，不走委托链。

harness Agent 循环（每个机器人一个）：
  while:
    LLM → tool_call（统一，不分本地/网络）
    本地工具 → 本机 sandbox → 结果
    网络工具 → 发 TASK 委托 → 对方也是 Agent 循环 → 同步等回传 → 结果
    → 结果喂回 LLM 下一轮（=复核）
    → 直到 LLM 不调工具 → 输出结果
```

## 二、保留 vs 重写

| 部分 | 处理 | 理由 |
|---|---|---|
| Task/TLLjson 结构 | **保留** | 外部机器人/系统依赖 to_dict/from_dict 契约 |
| 加密（ENCRYPTED_TASK/Fernet） | **保留** | 安全契约，换加密会破坏跨机器人通信 |
| MQTT topic（tll/agent/<id>） | **保留** | 网络寻址契约 |
| TaskStatus 枚举 | **保留** | 状态机契约 |
| record_lis 上报结构 | **保留** | Skaye-SV 接收端不变 |
| bot.yaml 加载 / peers / 鉴权 | **保留** | 机器人基础 |
| **executor/chat_tool/回传/复核** | **重写** | 改为 harness Agent 循环 |
| plan_task + JSON commands | **删除** | 旧模型遗留 |
| pending_queues 串行队列 | **删除** | harness while 循环替代 |
| 同步线程 on_message 阻塞 | **重写** | 改 async + 线程桥接 |

## 三、v2 目录结构

```
tll_protocol_v2/
  __init__.py         # 导出（兼容旧接口）
  core.py             # Task/TLLjson/TaskStatus/Trace（保留，复用旧）
  security.py         # 加密（保留，复用旧）
  mqtt.py             # MQTT 收发（改造：网络线程 → async 队列桥接）
  node.py             # 机器人节点（Bot v2：装配 harness Agent）
  router.py           # 收消息路由：TASK → 匹配 task_id 回传 或 启动新 Agent
  harness_bridge.py   # 从 bot 装配 harness ToolRuntime + Agent
  llm.py              # LLM 适配（复用 harness DeepSeek）
  report.py           # record_lis 上报（复用 harness ToolReport）
```

## 四、核心机制：回传桥接

```
MQTT 网络线程 on_message（同步）
  → 解析 TASK
  → 判断：是回传（task_id 在 _pending）→ handle_response(task_id, result) 填充 future
  → 是新任务 → 提交到 async 事件循环，启动新 Agent 循环
  → 是转发中间态 → 继续路由

harness Agent 循环（async，独立事件循环跑）
  → 调 task_create → TLLTransport 发 TASK → await future
  → 回传由 MQTT 线程 handle_response 填充 → 唤醒 Agent
```

## 五、保留契约（已确认，来自旧 tll_protocol/core.py + security.py）

### TaskStatus 枚举
```
CREATED='created', PENDING='pending', RUNNING='running', SUCCESS='success',
FAILED='failed', CHECK_REVIEW='check_review', RETURNING='returning',
DELEGATED='delegated'
```

### TLLjson
```python
TLLjson(from_bot, command, to, params=None, task_func=None)
```

### Trace / TraceHop
```python
TraceHop(bot, action, timestamp=ISO-8601)   → {bot, action, timestamp}
Trace(trace_id)  → {trace_id, hops:[...]}
```

### 加密（security.py）
```python
_fernet_key(auth_key): sha256(auth_key).digest() → base64.urlsafe_b64encode
encrypt_payload(data, auth_key): Fernet 加密，auth_key 空则原样
decrypt_payload(data, auth_key): Fernet 解密，失败则原样返回
```

### MQTT 消息格式（task_sender.py）
```python
明文: {type:"TASK", target, sender, timestamp, task: task.to_dict()}
加密: {type:"ENCRYPTED_TASK", target, sender, timestamp, ciphertext}
topic: tll/agent/<id>（由 topic_mapper 解析）
```

### 网络消息类型
- `TASK`：委托任务
- `ENCRYPTED_TASK`：加密的 TASK

### skill 契约（必须保留）
```python
skills/<name>/tool.yaml + tool.py 暴露 handle(params, bot=None, task=None, **kwargs)
返回 {"status":"success|error|continue","info","next","command","params"}
鉴权: bot.yaml tools[].access (allow/allow_groups/deny/deny_groups，支持 *)
```

### hook 契约（必须保留）
```python
dispatch(level, message, logger=None, task=None, **kwargs)
hook 签名: hook(message, logger=None, task=None, **kwargs)
HookEvent.to_dict() = {hook_name, level, task_id, message, timestamp, status, detail, task_info, source_bot}
级别: error/warning/success/info/debug/finish + bot_create
```

### route / 回退机制（重写时对齐）
```python
task.route = LIFO 委托栈
push_route=True  (向前委托): append 发送方 bot_id + sender_group + 状态→DELEGATED
push_route=False (回传): 不改 route/状态，状态由 process_return 前置 RETURNING
process_return 链: RETURNING/FAILED 记结果 → pending 队列推进 → 复核(CHECK_REVIEW)
  → DELEGATED/CHECK_REVIEW 跳过 → route.pop() 定 return_to
  → 有上级: RETURNING 回传; 无: SUCCESS/FAILED + finalize + 归档
```

## 六、harness 可复用组件（研究报告确认）

以下组件**可直接搬进 TLL 重写**：
- `agent.py` Agent 循环（async while 多步推理）
- `session.py` Session（append-only 会话日志、derive_messages）
- `registry.py` Registry + ToolRuntime + ToolCall
- `security/` 执行管线（审批 + 策略 + 沙箱）、CapabilityBackend
- `adapters/tll_transport.py` TLLTransport（含 handle_response 桥接 + TASK id 复用）
- `tools/task_create_tool.py` 网络委托工具
- `adapters/deepseek.py` DeepSeek 适配器
- `report.py` ToolReport（record_lis 上报）

### 精确构造签名（async 标记）
```python
Agent(llm, tool_runtime, options=None, session=None, bus=None)          # run 是 async
Session(session_id=None)                                                # 全同步
Registry()  register_tool/register_backend 全同步，返回 disposer
ToolRuntime(registry, pipeline, reload_hook=None)                       # execute async
ExecutionPipeline(policy_resolver, approval, default_verdict=ALLOW)     # execute async
SandboxPolicyResolver(default_mode, workspace_root, session_override)   # resolve 同步
CapabilityBackend seam: async execute(request, policy) -> ExecutionResult
ApprovalService / CallbackApprovalService: async request
TLLTransport(config: TLLTransportConfig): async execute                 # 同步等回传
task_create 工厂: create(config) -> ToolDefinition(backend="tll")
ToolReport(bot_yaml_path): 从 bot.yaml tool_list 生成上报
DeepSeekClient(api_key→env, base_url, model=deepseek-chat, timeout_ms=60000, http_opener=None)
MockLlmClient(script: (messages,tools) -> LlmResult)
```

### async 清单
- async：Agent.run、TLLTransport.execute、ExecutionPipeline.execute、ToolRuntime.execute、
  LlmClient.generate、CapabilityBackend.execute、ApprovalService.request
- 同步：Registry/Session/SandboxPolicy/EventBus/PluginLoader/ToolReport/handle_response

**需要新写的**：
- 真实 MQTT transport（mount/shutdown 实装 + 回调调 handle_response，新增 paho-mqtt 依赖）
- 真实审批 UI
- 异步 HTTP（当前 urllib 同步阻塞）
- 跨平台沙箱后端（JobObjectShell 仅 Windows）
- 多 tool_call 队列闭环扩展
- 动态 peers 表（从 ToolReport 发现填充）
- Agent._tool_schemas 私有访问接口化
- shutdown 时取消悬挂 future

**三处阻塞陷阱（注意）**：
1. urllib 同步 HTTP（LLM 请求会阻塞事件循环）
2. proc.communicate 阻塞（沙箱命令）
3. 多 tool_call 队列未闭环（多余 tool_call 不投影回模型，OpenAI 兼容 API 可能报错）

## 七、可推倒重来的旧模型遗留（研究报告确认）

- `llm.plan_task` / `create_task_json` 的 JSON reply+commands 规划模型
- 多命令队列：`Bot.pending_queues/queue_records`、`Task.command_queue/queue_results/last_target`、chat_tool 多命令分支、executor 队列推进段
- `send_once` 双发送路径（每次新建 paho 客户端，绕过传入 transport）
- `TLLjson.task_func` 遗留字段、`TaskInputModule`、`STATUS_COLORS` 颜色、`register_to_sv` 空操作
- `build_registration_info` 硬编码 related_map/peer 启发式
- bot_factory 二次 register、`Bot.to_dict`/`get_task_tracker` 本地追踪

## 八、风险提示

- `record_lis` 结构、`HookEvent` 字段、`registered_bots.json` 被 dashboard/monitor/trigger 消费，改动需同步监控侧
- `tll/skaye_SV`（大写）与 `tll/agent/skaye_sv` 并存硬编码，归一化要两端同时改
- 加密升级需全节点同步，属"重写"而非"丢弃"

