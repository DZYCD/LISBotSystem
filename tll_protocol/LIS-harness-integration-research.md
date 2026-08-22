# LIS v2 外部工作流研究报告（TLL 协议现状）

> 研究日期：2026-08
> 目的：在 TLL 升级前，摸清现有外部工作流，确定 LIS-harness 的插入点。

## 一、系统全景

```
E:\...\LIS_v2\
├── tll_protocol\        # TLL 协议引擎（机器人间通信）
├── bots\<bot_name>\     # 每个机器人：bot.yaml + start.py + skills/
└── ARCHITECTURE.md      # 系统蓝图（v0.2.0）
```

机器人分三类（ARCHITECTURE.md）：
- **SaYi**（调度）、**EiAr**（行动，我是 eiar_001）、**Skaye**（监控）
- **SV**（SuperVisor）：`sayi_sv` / `skaye_sv` 由主人扮演，中央调度

## 二、外部工作流核心：TASK + TLLjson + MQTT

### 消息模型（core.py / bot.py）
所有交互封装为 **TASK** 对象：
- `id` / `type`(dialog/tool/router/routine) / `status`(pending/running/success/failed/...)
- `tlljson`：委托鉴权声明（from/to/command/params/auth）
- `trace`：链路追踪（trace_id + hops）

### 传输（mqtt_transport.py / task_sender.py / receiver.py）
- 用 **paho-mqtt** 收发，`client_id` = bot id，订阅自己的 topic
- 加密消息（`type=ENCRYPTED_TASK`）用 auth_key 解密
- `TaskReceiver`：bytes/str/dict → Task（只解析，不执行）

### 工具执行（executor.py）
`TaskExecutor.execute(task)` 是外部工具调用的核心入口：
```
check_task()  →  command 是否在 handler_map
authorize()   →  访问控制（access.allow/deny 白黑名单）
handler = handler_map[command]
result = handler(params)     ← 调用 bot 的 skills/chat 处理函数
根据 result.status：success / error / continue（转发给下一机器人）
process_return() → 复核（LLM 判断结果是否满足）→ 回传给委托方
```

## 三、LLM 内嵌调用工具的方式（现状）

`llm.py` 的 `LLMClient.plan_task()`：
- **提示词拼接式**，非原生 tool-calling：
  - system prompt = 角色设定 + 说话规则 + 上下文历史 + 工具调用规则（`tool_rules.yaml`）
  - 让 LLM 输出 JSON：`{"reply": "...", "commands": [{"target": "...", "command": "...", "params": {}}]}`
  - 用 `_extract_json()` 正则剥离代码块提取 JSON
- `commands` 是委托数组：LLM 决定联系哪个机器人、调什么工具
- 用 **OpenAI SDK**（`from openai import OpenAI`），base_url 可指向 DeepSeek

### 关键机制：commands → 委托
LLM 输出 `commands` 后，创建 TASK 通过 `send_command` 委托给目标机器人。
`executor` 里的 `status: continue` 支持链式转发（A→B→C）。

### 复核循环（executor.process_return）
工具结果返回后，若 `original_text` 存在，用 LLM 做 **CHECK_REVIEW**：
把工具执行结果喂回 LLM，判断"是否满足请求"，不满足继续处理。

## 四、当前架构的两个核心局限（与 harness 对比）

| 维度 | 现状（TLL） | LIS-harness |
|---|---|---|
| LLM 调工具 | 提示词拼 JSON，正则提取，**不可靠** | 原生 tool-calling，结构化 |
| 工具执行 | `handler_map[command]` 直接调用，**无沙箱** | 受保护管线（审批 + 沙箱 + 策略） |
| 会话记忆 | history_manager（JSONL，token 统计） | append-only 会话日志 + 回放 |
| 热加载 | `bot.reload()` 重读 YAML（半成品） | PluginLoader 调用前 mtime 重载 |
| 模型适配 | OpenAI SDK 硬编码 | LlmClient 抽象，可替换 |

## 五、LIS-harness 插入点（推荐）

你的意图已明确：
- **外层**：TLL 协议负责机器人间对话/委托（MQTT 收发）
- **内层**：LLM 内嵌在 harness 里负责"自己调用自己的工具"

推荐整合结构：
```
MQTT (TLL 协议) ──►  Bot.receiver ──►  LIS-harness Agent 运行时
                                          │  (LLM 内嵌 + 工具沙箱 + 会话日志)
                                        ▼
                                       工具执行（受保护管线）
```

具体切入：
1. **`executor.execute` 的 `chat` 命令处理**：目前 `chat` 走 `chat_tool.handle` →
   `llm.plan_task` → JSON commands。可替换为：harness Agent 的 `run()`，
   让模型用原生 tool-calling 调用**本地工具**（而非委托给其他机器人）。
2. **本地工具**进 harness 的 Registry + 沙箱；**跨机器人工具**仍走 TLL 委托。
3. **会话日志**替换 history_manager，作为记忆与真相源。

## 六、风险与注意

- `chat_tool.py` 是跨机器人统一对话入口，替换要兼容现有 reply 格式
- `executor.process_return` 的 CHECK_REVIEW 依赖 LLM，替换时要注意
- MQTT 用 paho-mqtt；harness 目前无 MQTT，接入点在 transport 层
- 现有 `llm.py` 用 OpenAI SDK，harness 用 urllib，两套并存期要注意
