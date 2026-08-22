# LIS-harness 与 TLL 整合设计方案：TASK CREATE 工具化

> 状态：设计方案（待实现）
> 目标：在不改动现有 TLL executor/协议/MQTT 的前提下，把「任务创建」交给
>       harness 作为一个工具，让 LLM 用原生 tool-calling 调 task_create 委托任务。

## 〇、统一模型（用户的架构本质）

**所有网络活动都是委托链；网络委托 = 调用网络工具（请求工具名 + 参数字典）；
自己用自己工具 = 本地请求，不走委托链。**

```
一次"工具调用"（无论本地/网络）：
  LLM 发出调用请求
  → 本地: 本机 sandbox 执行 → 结果
  → 网络: 委托别的机器人 → 对方走它自己的 LLM 循环 → 回传结果
  → LLM 下一轮处理这个结果（= 复核）
  → 继续判断：再调工具 / 结束输出
```

**复核 = 等工具调用结果回来后的那一轮 LLM**。委托链 = 嵌套的 LLM 循环栈，
靠"网络工具调用 + 回传结果"串起来。全系统 = 一个个 LIS-harness 的 agent
循环，通过网络委托互连。

## 〇.五、现状代码 vs 统一模型的差距（已检查确认）

| 你的模型 | 现状 chat_tool | 一致？ |
|---|---|---|
| LLM while 循环 | 单次 plan_task，靠回传再触发一轮 | ⚠️ 逻辑等价，非真循环 |
| 工具调用=tool-call（结构化） | LLM 裸拼 JSON commands + 正则提取 | ❌ 不一致 |
| 复核=等工具结果那轮 LLM | 结果拼进 text 再走 plan_task | ⚠️ 语义等价，用字符串拼接 |
| **本机本地工具调用** | **现状没有本地工具环节** | ❌ **最大缺口** |
| 网络委托=调用工具 | commands 里 {target,command,params} | ⚠️ 形式接近，经正则提取 |

**关键结论**：现状 `chat_tool` 的 LLM 循环只能「网络委托」或「直接回复」，
**没有「本机跑工具」的环节**。而 harness 的 `Agent.run` 是真 while 循环 +
结构化 tool-call + 本地工具沙箱。**整合 = 用 harness 的 Agent 循环替代
chat_tool 的单次 plan_task，让本地工具和网络委托都在同一个循环里。**

## 〇.六、整合方案（用户确认）：harness 改造 LLM 核心

**把 harness 拿过来改造 LLM 核心，替换 chat_tool 的 plan_task。**

```
Agent 循环（harness 的 run，替代 chat_tool）:
  LLM → 输出 tool_call（统一格式，不分本地/网络）
  → harness 分派：
     本地工具 (bash/file_read)  → 本机 sandbox 执行
     网络工具 (task_create)     → 发 TASK 委托 → 同步等对方 LLM 循环回传
  → 构造 message 结果（tool-result）
  → 喂回 LLM 下一轮
  → 直到 LLM 不调工具 → 输出结果
```

**关键设计决策（已确认）：**

| 决策 | 说明 |
|---|---|
| **统一 tool_call** | 本地/网络请求都用同一 tool_call 机制，不分两套 |
| **message 构造等待返回** | 本地返回 / 网络回传都构造成同一种 tool-result 喂回 LLM |
| **同步阻塞** | task_create 被调时同步等网络回传，结果作为普通 tool-result 返回。实现简单，LLM 视角统一 |
| **TASK id 复用** | LLM 若在被委托进来的 task 上下文中再委托，复用当前 task.id（保持委托链连贯，同现状 continue 逻辑）；LLM 独立发起则新建 task |
| **一次一个 tool_call（串行）** | 模型即使一次输出多个 tool_calls，也逐个串行：执行第一个 → tool-result 喂回 → LLM 再决定。对齐现状 pending_queues 队列委托，避免"多 TASK id 并行"复杂度 |

**多工具调用的决策（已确认）**：OpenAI/DeepSeek 支持模型一次输出多个
tool_calls（parallel tool calls）。harness 采用**串行**处理——一次只执行一个，
避免"多网络委托并行 + 多 TASK id 管理"。对模型多输出的防御：取第一个执行，
其余丢弃或缓存，不报错不崩溃。这与现状 `pending_queues` 的串行队列语义等价。

**复核语义**：网络回传结果作为 tool-result 进入 LLM 下一轮（=复核），
与本地工具结果完全一致。复核本质 = 等工具调用结果。

## 〇.七、整合后的回传机制（用户确认）

**委托和本地工具调用已合并为一个方法（统一 tool_call）。** 工具调用后的
结果回传机制：

```
harness Agent 调 task_create（网络工具）
  → 发 TASK 委托出去
  → 同步阻塞等待回传

回传分两种：
  网络回传 → TLL 负责把回传结果「匹配到当前阻塞的 task_id」，交给对应 TLL
            → harness TLLTransport.handle_response(task_id, result) 填充 future
            → 唤醒阻塞中的 Agent
  本地回传 → 工具在本机执行完，结果正常返回（不走网络）
```

**TLL 的新职责**：把网络回传消息匹配到「正在阻塞等待对应 task_id 的 harness
Agent」，调用 handle_response 填充结果。本地工具不经此路。

**桥接**：TLL 收到回传时（on_message 网络线程），解析 task_id，调
TLLTransport.handle_response(task_id, result) 填充线程安全的 future，唤醒
阻塞中的 Agent。

## 〇.八、接入 chat_tool 的架构

```
chat_tool.handle(params, bot, task):
  → 构建 harness Agent + ToolRuntime（从 bot 的 skills/peers 装配）
  → agent.run(text)  ← 统一 tool_call 循环（本地 + 网络）
  → 返回回传结果

接入点：
  executor.execute → handler_map['chat'] → chat_tool.handle（改为 harness）
  本地工具  → harness 本机 sandbox（已实现）
  网络委托  → task_create → TLLTransport 发 TASK → 同步等回传（handle_response 桥接）
```

**技术难点**：harness 是 async，TLL 是同步多线程（paho loop_start + on_message
网络线程）。需要线程模型处理（handle 用独立线程跑，避免阻塞 MQTT 网络线程）。

## 一、背景与动机

现有 `chat_tool.handle` 的 LLM 调工具方式是：
```
LLM → plan_task() 输出 JSON {"reply":..., "commands":[...]}
     → 正则提取 JSON → 逐条 create_task → send_command 委托
```
问题：模型裸拼 JSON，靠提示词约束 + 正则提取，**结构易错、不可靠**。

目标：LLM 通过 harness 原生 tool-calling 调用 `task_create` 工具，参数被
schema 校验，比模型拼 JSON 可靠得多。

## 二、分层（架构不变，只动任务创建）

```
bot.yaml (TLL 层)               harness yaml (工具层)
├── name/id/network             ├── tools: 本地工具(bash/fs) + task_create
├── peers（可委托谁）            ├── backends: shell / tll
├── auth_key                    └── llm 配置
└── tools（工具声明，单一来源）
        │
        ▼ 合并（都塞进 bot.yaml，TLL 和 harness 按需读取）
LIS-harness 核心
  ├── LLM（原生 tool-calling）
  ├── 工具运行时（沙箱治理本地工具）
  └── task_create 工具 → TLL transport → 发 TASK 给其他机器人
```

## 三、关键设计决策

### 1. 配置合并：都塞进 bot.yaml，单一来源

- 工具声明放 bot.yaml 的 `tools:` 段，**TLL 的 handler_map 和 harness 的
  Registry 共享同一份**（按需读取）。
- 理由（用户洞察）：一个工具可能同时被 LLM 调用、也被网络（其他机器人）调用，
  所以工具注册信息必须是单一来源。
- ⚠️ 坑：`BotConfig(**data)` 会展开整个 bot.yaml，任何 BotConfig 没有的新字段
  会 TypeError。必须扩展 BotConfig 支持 harness 相关字段。

### 2. task_create 工具：新建，但保留原通道

- 新建 `task_create` 工具注册进 harness，参数 schema 校验。
- **保留**现有 `chat_tool`/delegate 委托通道——因为存在非 LLM 调用场景
  （如其他机器人直接通过 TLL 委托，不经 LLM）。

### 3. 报错反应作为委托链一环（用户愿景，后续）

- 工具错误不是原地报错，而是回传到调用的 SaYi。
- SaYi 与 Skaye 一对一搭档；报错时 SaYi 找 Skaye 修。
- Skaye 检查工具名/鉴权/params/调用规则，返回解决报告给 SaYi。
- harness 层对应：task_create 工具的错误结果应带上足够上下文，供上层回传。

## 四、实现步骤（第一步：合并 + 工具原型，不动现有 TLL）

1. ✅ 扩展 `BotConfig`：加 `harness` 字段（可选 dict），避免 `**data` TypeError。
2. ✅ 在 LIS-harness 里做 `task_create` 工具（模拟 TLL transport 验证）。
3. ✅ 合并原型（`proto_merge.py`）：
   - 读真实 `eiar_001/bot.yaml` 的 peers/tools
   - 用 peers 配置 TLL transport 白名单
   - 注册 task_create 工具
   - 验证 LLM 通过 harness 调 task_create 能创建委托（已验证 [OK]）
4. ✅ 工具单一来源（`skill_loader.py` + `proto_skills.py`）：
   - harness 从 `skills/<name>/tool.yaml` + `tool.py` 动态注册工具
   - TLL 的 handler_map 与 harness 的 Registry 共享同一份来源
   - 无 parameters 声明时用宽松 schema；工具依赖缺失时容错跳过
   - 验证：从 eiar_002/skills 加载，LLM 能调用（已验证 [OK]）
5. 后续（接入真实）：把模拟 transport 换成真实 MQTT。

## 五、涉及文件

| 文件 | 改动 |
|---|---|
| `tll_protocol/bot.py` | BotConfig 加 harness 字段 |
| `LIS-harness/.../tools/task_create_tool.py` | 新建 task_create 工具 |
| `LIS-harness/.../adapters/tll_transport.py` | 模拟 transport（已有） |
| `proto_merge.py` | 合并原型脚本 |
