# LIS-harness 内部架构（LLM 的内在）

本文档讲的是 `lis_harness/` 这个核心包的内部机制——一个机器人**单个节点**如何用 LLM 完成"思考 → 调用工具 → 拿到结果 → 再思考"的多步推理。这对应"LLM 的内在"。

> 想了解机器人**之间**怎么通讯、怎么委托任务，看另一份文档 [`protocol.md`](./protocol.md)（LLM 的社区）。

## 0. 一句话概览

`lis_harness` 是一个**可学习的 agent 安全骨架**，复刻 deepseek-harness 的「执行前审批 + 每次调用动态解析的范围策略 + 受保护执行管线」机制。它把 **LLM 循环** 和 **工具执行** 串成一条安全管线：模型每次想调用工具，都必须先过审批和沙箱策略，再真正执行。

```
LLM（生成文本或 tool-call）
  │
  ▼
Agent 循环（多步推理引擎）
  │  每次 tool-call
  ▼
ToolRuntime ──► ExecutionPipeline（审批 + 策略解析 + 沙箱执行）──► 能力后端
  │
  └──► 结果写回会话日志，喂回 LLM 继续推理
```

## 1. 模块总览

`lis_harness/` 核心模块及职责：

| 模块                | 职责                               |
| ----------------- | -------------------------------- |
| `agent.py`        | Agent 循环：多步推理引擎（核心）              |
| `llm.py`          | LLM 客户端抽象：agent 循环通过它调模型         |
| `registry.py`     | 工具定义与注册中心 + ToolRuntime（受保护执行入口） |
| `loader.py`       | 插件加载器：YAML 声明 + Python 实现，支持热重载  |
| `skill_loader.py` | 技能加载器：从 skills/ 目录动态注册工具（单一来源）   |
| `session.py`      | 会话日志：append-only 事件流（唯一真相源）      |
| `events.py`       | 轻量事件总线：插件/哨兵之间通信                 |
| `sentries.py`     | 哨兵插件：展示三种运行模式                    |
| `report.py`       | 上报器：从工具清单生成"可对网络开放工具"清单          |
| `security/`       | 安全管线：审批 + 策略解析 + 沙箱后端            |

## 2. Agent 循环（多步推理引擎）

`Agent.run(user_text)` 是核心。本质是一个 `while` 循环：

```
turn 开始
  for step in 1..max_steps:
    组装消息（历史 + 工具 schema）→ 调 LLM generate()
    若模型输出 tool-call 块:
       逐个串行执行（审批 + 沙箱）→ 写 tool/result
       → 回到 step（模型看到工具结果，继续推理）
    否则（无 tool-call）:
      → 完成，返回最后文本
turn 结束
```

### 关键设计点

- **多步推理**：模型说"我要调工具"，harness 调完把结果喂回去，模型再接着想，直到不再调工具。这就是多步推理的本质。
- **串行执行 tool_calls**：一轮 LLM 可能生成多个 tool-call，`agent.py` **逐个依次执行**，每个都有对应 `tool/result` 写回日志。DeepSeek 要求每个 tool_call 都有匹配的 tool 结果，串行保证不丢失。
- **失败闭环**：工具执行无论成功失败，都保证有 `tool/result` 写回喂给模型（`[error] ...` 文本），让模型看到失败原因并自纠，而不是让循环崩溃。
- **`max_steps` 上限**（默认 10）：防止无限工具循环。
- **坏 JSON 参数保护**：`_run_tool_call` 解析工具参数，坏 JSON 直接返回错误，而非静默当空参数执行。

## 3. 会话日志（唯一真相源）

`Session` 是 **append-only 事件流**，记录 agent 的每一步：
`turn/start` → `step/start` → `user/message` → `assistant/message` → `tool/call` → `tool/result` → `step/end` → `turn/end`。

- 每次 LLM 请求都从日志 `derive_messages()` 组装（历史上下文）。
- **"模型可见 ⟺ 已记录"**：任何到达模型的内容都能从会话日志重建。
- 跨消息持久会话：`node._sessions` 按对话方维护，跨消息记忆，且用 `_session_has_system()` 防止 system 提示词重复注入。

## 4. 系统提示词分层（缓存优化）

DeepSeek 等按"请求前缀"做 prompt caching：从第一条消息开始连续相同的部分被缓存，命中便宜且快。

因此 `system_layers` 把**稳定内容（角色/工具）放前面的层**，把每次变化的会话内容放最后，最大化前缀缓存命中率。提供 layers 则优先用，否则退回单层 `system_prompt`。

## 5. 工具注册与执行管线（安全核心）

### 注册中心 Registry

- `register_tool(ToolDefinition)` / `register_backend(name, backend)`：工具定义 + 能力后端双注册。
- **同名工具重复注册抛错**（显式 > 隐式，不允许静默覆盖）。
- 支持 disposer（卸载），用于热重载场景。

### ToolRuntime：受保护执行入口

`ToolRuntime.execute(call)` 把一次工具调用接进执行管线：

1. 从 Registry 解析工具定义 → 取能力后端
2. 构造 `ExecutionRequest`（含 actor 身份）
3. 走 `ExecutionPipeline.execute()`（审批 + 策略 + 沙箱）

### ExecutionPipeline：审批 + 策略 + 沙箱

```
pre-execute（审批决策：allow/deny/ask）
  ▼
解析 SandboxPolicy（本次调用动态确定沙箱边界）
  ▼
backend.execute(request, policy)（能力后端真正执行）
```

### 沙箱后端（security/backends/）

| 后端             | 能力                                                                             |
| -------------- | ------------------------------------------------------------------------------ |
| `jobshell.py`  | JobObjectShell：Windows Job Object 治理真实子进程（进程数/内存/超时），命令只在 DANGER 档放行，读写走路径范围检查 |
| `winjob.py`    | Windows Job 对象底层：subprocess + Job 分配、超时强制终止                                    |
| `inprocess.py` | 进程内沙箱：路径范围检查                                                                   |

### 工具实现（tools/）

- `bash_tool.py`：bash 工具定义示例（backend="shell"，热重载测试目标）
- `task_create_tool.py`：网络委托工具（跨节点）

## 6. 能力后端 seam（Implements 路由）

每个机器人从 `config/tools.yaml` 声明工具，`implements` 字段决定用哪个后端：

| implements     | 后端                                                         |
| -------------- | ---------------------------------------------------------- |
| `local`        | `_LocalFileBackend`（文件操作：file_read/file_write/...）         |
| `contact`      | contact_tools（牵线接口，Skaye 族可调）                              |
| `skaye_perm`   | skaye_tools（Skaye 自权限管理）                                   |
| `skill:<name>` | SkillLoader 从 skills/<name> 加载 tool.py 的 handle            |
| `code`         | `_CodeSandboxBackend`（包装 JobObjectShell，沙箱执行 Python/shell） |

`ping` / `LISreport` 是强加载工具，由运行时统一注册（返回完整注册信息），不从工具清单重复注册。

## 7. 技能加载器（skill_loader.py）

"单一来源"：工具声明在 `skills/<name>/tool.yaml`（+ `tool.py` 实现），harness 从同一份生成 ToolDefinition。

- `scan()`：扫描 skills 目录，读取 tool.yaml 的 name/description/parameters。
- `load_into()` / `load_one()`：注册 SkillBackend + ToolDefinition 进 Registry。
- `_load_handler()`：动态 import tool.py 的 `handle` 函数；导入失败（如依赖未装）则返回 None，工具不注册。

> **注意**：依赖未安装会导致 skill 静默不注册（如 search 依赖 bs4）。这是"功能声明了却不能用"的常见原因。

## 8. LLM 客户端抽象

`LlmClient.generate(messages, tools) -> LlmResult`：给定对话历史 + 可用工具 schema，返回一轮生成的块（文本块 和/或 tool-call 块）。

- `LlmResult.blocks`：本轮产出（TextBlock / ToolCallBlock / ReasoningBlock）。
- `MockLlmClient`：脚本化模拟，供循环和日志测试跑通（隔离验证）。
- 真实 DeepSeek 客户端在 TLL v2 侧实现（见 protocol.md 或 `tll_protocol_v2/`），通过同一 `generate` 接口接入。

## 9. 典型工具调用生命周期

以 eiar_001 的 `run_code`（沙箱执行 Python）为例：

```
1. 模型生成 tool-call: {name:"run_code", arguments:{code:"print(1+1)"}}
2. Agent._execute_tool_call 写 tool/call 日志
3. ToolRuntime.execute → ExecutionPipeline（审批 ALLOW + DANGER 策略）
4. _CodeSandboxBackend 把 code 写入临时 .py 文件，转成 "python <tmp>.py" 命令
5. JobObjectShell 用 Windows Job 启动子进程执行，捕获 stdout/stderr/exit_code
6. 结果 JSON 写回 tool/result 日志，喂回模型
7. 模型看到 "stdout: 2", 组织最终回复
```

## 10. 与 TLL v2 的关系

`lis_harness` 是"单个节点的引擎"，`tll_protocol_v2/` 是"节点间的网络协议"。节点装配（`tll_protocol_v2/node.py`）：

```
bot.yaml → NodeConfig（peers/auth_key/llm/skills）
    └── 装配 Registry + ExecutionPipeline + ToolRuntime + Agent
    └── 装配 V2TLLTransport（网络委托）+ MQTT（收发）
```

一个节点收到任务后，跑 harness Agent 循环（LLM 内在）；LLM 若要别的机器人做事，用 `task_create` 网络委托（走 TLL 协议，见 protocol.md）。
