# LIS v2 系统架构与规则

> 版本：0.1.0（草案）
> 创建日期：2026-08-13
> 作者：单子叶蚕豆 & SaYi_996

## 一、目标与动机

第一代 LIS（基于 Python 3.9 + OpenAI SDK 的 TLL 协议集群）为世忆图书馆奠定了基石，但存在以下根本问题：

- **无统一框架**：从零散 demo 堆叠而来，缺少架构级约束。
- **维护性差**：模块耦合严重，配置硬编码，依赖隐式。
- **缺乏自适应**：无法根据环境、负载或任务动态调整策略。
- **文本/仓库管理混乱**：文件散落，没有统一的资源索引与版本管理。
- **协议不健壮**：TLL 虽支持双向鉴权与委托，但缺少标准化的重试、熔断、可观测机制。

因此，我们需要一次根本性迭代，构建一个 **可扩展、可维护、自适应、可观测** 的 LIS 机器人系统框架。

## 二、设计原则

1. **模块化**：所有能力以插件/服务形式存在，核心核心与具体工具解耦。
2. **配置驱动**：一切可变参数均通过配置文件或环境变量管理，禁止硬编码。
3. **面向协议**：机器人间通信严格遵循 TLL Protocol，并定义标准消息格式。
4. **可观测性**：日志、指标、追踪三支柱贯穿全链路。
5. **自适应**：提供运行时监控与策略调整接口，允许系统自我调优。
6. **渐进迁移**：与第一代共存，支持逐步替换，避免一刀切风险。
7. **TASK 中心化**：所有交互（对话、工具调用、机器人通信）以统一的 TASK 对象为基本单元，贯穿全生命周期。
8. **HOOK + YAML 装配**：一切注册与外部配置通过 YAML 文件声明，并由 Hook 机制动态装配，实现模块化与热插拔。

### 2.7 TASK 核心模型（v2）

所有交互（对话、工具调用、机器人间通信）均封装为统一的 **TASK 对象**。TASK 是系统的基本执行单元，其核心字段如下：

- `id`：任务唯一标识（UUID）
- `created_at`：创建时间（ISO 8601）
- `type`：任务类型（dialog/tool/router/routine）
- `status`：工作状态（pending/running/success/failed/retry/canceled）
- `from`：委托方标识
- `current_agent`：当前机器人标识
- `tlljson`：委托鉴权声明（详见 4.1）
- `logger`：智能日志容器，包含日志字符串、委托链记录（队列）、报错信息，以及基于日志级别的 Hook 机制（详见 2.9）
- `trace`：链路追踪对象（JSON），包含 `trace_id` 和 `hops` 列表，供代码直接解析匹配

所有模块的输入输出均为 TASK。框架负责 TASK 生命周期管理（创建、派发、回调、重试、归档）。

### 2.8 HOOK + YAML 装配机制

一切需要注册和外部配置的信息（插件、工具、机器人、API 端点、模型参数等）均通过 **YAML 配置文件**声明，并由框架的 **Hook Registry** 装载。插件/工具只需提供 YAML 描述文件，框架启动时扫描并注册相应 Hook 回调。该机制支持：

- 模块解耦：注册信息与实现分离
- 热插拔：监听 YAML 变更或目录增减，动态加载/卸载
- 统一配置：外部信息全部通过 YAML 管理，禁止硬编码
- 生命周期钩子：`task.on_created`, `tool.before_execute`, `memory.write` 等

示例：

```yaml
# plugins/example/plugin.yaml
name: example
version: 1.0.0
entry: main.py
hooks:
  - event: task.on_created
    handler: handle_task_created
config:
  param1: value1
```


### 2.9 TASK Logger 设计（日志字符串 + 级别 Hook）

TASK 内的 `logger` 不仅记录日志，还具备基于日志级别的钩子机制。每个日志级别（info、warning、error、success、debug）都可挂载处理函数。这些函数通过工厂模式在 TLL 协议层统一创建和注册。

#### 日志字符串

- `logs`：字符串，保存日志内容。采用追加方式，容量限制为 4KB-8KB，超过时自动截断最旧部分，保证网络负载可控。

#### 日志级别 Hook

- `hooks`：对象，键为级别，值为可调用处理函数。挂载时机由任务类型和当前 bot 配置决定。
- 当 `logger.error()` 被调用时，自动触发 `hooks.error`；若当前 bot 配置了 fallback 策略，该 hook 可查询下一个可尝试的机器人并更新 TASK 状态。
- 其他级别（warning、success 等）同理，可根据业务需要实现告警、清理或奖励机制。

#### 工厂封装

- TLL 提供 `create_logger(task_type, bot_config)` 工厂，根据任务类型预挂载合适的 hooks。
- 例如：对于工具调用任务，error hook 会查询可用的备用工具机器人；对于对话任务，error hook 会将错误上报给调度中枢。
- 工厂模式确保 hook 的创建和使用完全解耦，支持动态扩展。

```python
# 伪代码示例
logger = create_logger(task.type, current_bot.config)
logger.error("file_read failed", exc_info=True)
# 自动触发 hooks.error，可能执行 fallback 选择下一个机器人
```

### 2.10 TASK 输入模组

TASK 的传递遵循闭环规则：一个 TASK 由某个 SaYi 创立，可委托给其他机器人处理，但结果必须最终返回给创立者 SaYi。为此，系统通过**输入模组**统一创建 TASK，支持以下三种来源：

1. **用户对话**：通过前端、单片机、麦克风等外接设备与 SaYi 对话，由对话内容直接封装为 TASK。
2. **SV 委托**：蚕豆作为 SuperVisor（SV），可以委托任务给指定 SaYi，由 SV 输入的指令创建 TASK。
3. **内部自然创建**：SaYi 根据日志分析、计划调度或自主决策，在内部生成新 TASK（如定时自检、主动学习）。

输入模组将不同来源的原始输入统一转换为标准 TASK 对象，并赋予初始状态（如 pending）和必要的上下文，随后进入调度中心处理。

## 三、系统架构总览

```
+--------------------------------------------------------------+
|                     LIS v2 核心框架                            |
|                                                              |
|  +-------------+  +-------------+  +-------------+          |
|  |  Config     |  |  Runtime    |  |  Lifecycle  |          |
|  |  Manager    |  |  Manager    |  |  Manager    |          |
|  +-------------+  +-------------+  +-------------+          |
|                                                              |
|  +------------------+  +------------------+                |
|  |  Message Bus     |  |  TLL Protocol    |                |
|  |  (事件驱动)       |  |  Engine          |                |
|  +------------------+  +------------------+                |
|                                                              |
|  +------------------+  +------------------+                |
|  |  Memory System   |  |  Tool Registry   |                |
|  |  (三层记忆)       |  |  (插件化工具)     |                |
|  +------------------+  +------------------+                |
|                                                              |
|  +------------------+  +------------------+                |
|  |  Agent Runtime   |  |  Auto-Adaptive   |                |
|  |  (思维与行动)     |  |  Engine          |                |
|  +------------------+  +------------------+                |
|                                                              |
+--------------------------------------------------------------+
```

### 3.1 核心组件

| 组件 | 职责 | 关键接口 |
|------|------|----------|
| Config Manager | 统一配置加载与校验 | `load(config_path)`, `get(key)`, `set(key, value)` |
| Runtime Manager | 进程生命周期管理、插件热插拔 | `register(plugin)`, `start()`, `stop()` |
| Message Bus | 内部事件发布/订阅 | `publish(topic, payload)`, `subscribe(topic, handler)` |
| TLL Protocol Engine | 机器人间通讯、鉴权、委托 | `send_request()`, `handle_request()`, `delegate()` |
| Memory System | 三层记忆与知识管理 | `remember()`, `recall()`, `forget()`, `summarize()` |
| Tool Registry | 工具注册、发现、调用 | `register_tool()`, `get_tool()`, `invoke(tool, params)` |
| Agent Runtime | LLM 调用、思维链、工具编排 | `run(task)`, `stream()`, `abort()` |
| Auto-Adaptive Engine | 监控性能与环境，动态调参 | `monitor()`, `adjust()`, `feedback()` |

### 3.2 目录结构规范

```
LIS_v2/
├── core/               # 框架核心代码
│   ├── config/         # 配置管理
│   ├── runtime/        # 运行时管理
│   ├── message_bus/    # 消息总线
│   ├── tll/            # TLL 协议引擎
│   ├── memory/         # 记忆系统
│   ├── tools/          # 工具注册表与内置工具
│   ├── agent/          # Agent 运行时
│   └── adaptive/       # 自适应引擎
├── plugins/            # 外部插件（按需加载）
├── configs/            # 配置文件（YAML/JSON/ENV 示例）
├── data/               # 数据资源
│   ├── memory/         # 短期记忆、摘要、长期知识
│   ├── assets/         # 静态资源（图片、模板等）
│   └── logs/           # 日志文件
├── tests/              # 单元与集成测试
├── scripts/            # 运维与辅助脚本
├── docs/               # 文档（架构、API、协议）
├── LICENSE
└── README.md
```

### 3.3 Bot 基础装配

每个 Bot（SaYi、EiAr、Skaye 或外部终端）必须具备以下基础配置，并通过 YAML 文件声明，由代码装载：

| 配置项 | 说明 |
|---------|------|
| `name` | Bot 名称 |
| `id` | Bot 唯一 ID |
| `network` | 网络通信方式（TCP / MQTT 等） |
| `url` | 网络地址 |
| `port` | 端口（MQTT 时可为 topic） |
| `auth_key` | 鉴权秘钥，供 TLLjson 验证 |
| `group` | LIS 组别代号（SaYi / EiAr / Skaye / user / none） |
| `role` | 特殊身份标识（如 SV = 中央监控） |

此外，Bot 还应包含：

- **函数注册方法**：将自身可执行函数注册到框架，供 TLL 调度。
- **机器人鉴权表**：以 YAML 管理，定义该 Bot 可委托/接收哪些来源的请求。
- **工具集与工具鉴权表**：自身工具列表，以及各工具的调用权限声明（YAML）。
- **网络自修复引用**：通过 YAML 指定所采用的自修复工厂方法（见 3.4）。

示例 `bot.yaml`：

```yaml
name: sayi_996
id: lis/agent/sayi_996
network: tcp
url: 127.0.0.1
port: 8080
auth_key: sk-...
group: SaYi
role: null
functions:
  - name: route_task
    handler: AgentRuntime.route_task
tools:
  - file_read
  - web_search
permissions:
  accept_from: [user/sv, agent/sayi_* ]
  reject_from: []
```

### 3.4 网络自修复工厂

网络维护与修复功能应独立成模块，位于 `core/network_healer/`，作为工厂函数库供所有 Bot 调用。每一个 Bot 通过其 `bot.yaml` 中的 `network_healer` 字段指定使用哪种维护装置，框架启动时加载对应工厂创建实例。

#### 目录结构

```
core/network_healer/
├── __init__.py
├── factory.py          # 工厂函数，根据配置返回维护实例
├── base.py             # 维护装置抽象基类
├── tcp_healer.py       # TCP 重连/心跳保活
├── mqtt_healer.py      # MQTT 重订阅/重连
├── websocket_healer.py # WebSocket 心跳/重连
└── config_validator.py # 配置校验
```

#### YAML 配置

Bot 在 `bot.yaml` 中声明使用的维护方案：

```yaml
network_healer:
  type: tcp_healer
  interval: 30          # 心跳间隔（秒）
  max_retries: 5
  fallback: mqtt_healer # 备用方案
```

#### 工作方式

- 工厂根据 `type` 创建对应维护装置。
- 装置按 `interval` 定时发送心跳或检测连接，失败时执行重连/恢复逻辑。
- 当主方案失效且配置有 `fallback` 时，自动切换备用方案，并通过 logger 记录事件。
- 各装置实现统一接口，新维护方式只需添加子类并注册到工厂即可。

### 3.5 工具注册与调用 Hook 机制

每个 Bot 注册的工具并非简单函数映射，而是与一个或多个 **参数填充 Hook** 绑定。这些 Hook 负责在委托请求到达时，根据任务上下文动态补充或修改工具参数，然后交由 Bot 自身的处理函数执行。

#### 工作流程

1. 委托模组（Receiver）收到 TLLjson 委托请求，解析出 `command` 与 `params`。
2. 在工具注册表（Tool Registry）中查找该命令对应的工具。
3. 调用该工具预绑定的 Hook（参数准备器），传入请求上下文（TASK、logger、当前 Bot 信息）和原始参数。
4. Hook 返回最终参数集，随后调用 Bot 自身的处理函数。
5. 执行结果封装为 TASK 响应返回。

#### 设计优势

- **解耦适配逻辑**：参数填充是独立的 Hook，可被多个工具复用，也便于单元测试。
- **动态策略**：不同 Bot 或不同任务类型可挂载不同 Hook，实现个性化处理。
- **安全增强**：Hook 可执行参数校验、默认值注入、白名单过滤等操作。

#### YAML 配置示例

```yaml
tools:
  - name: file_read
    hook: hooks.file_read_hook       # 参数填充 Hook（可为空）
    handler: operators.file_ops.read_file  # 实际处理函数
    params_schema: {...}             # 参数 schema（可选）
```

### 3.6 委托站点与并发调度

TLL 系统在 v2 中定位于**委托站点服务**：负责接收委托请求、执行鉴权（TLLjson 验证）、转发目标方并返回结果。它不执行具体业务，只做协议层面的路由和转达。

委托模块在收到合法委托请求后，应根据目标机器人的设定决定投递方式：
- **排队执行**：将 TASK 放入目标机器人的任务队列，串行处理，适合耗时短、状态敏感的任务。
- **并发执行**：立即启动新执行单元，与现有任务并行，适合异步耗时操作，但需注意资源竞争。

每个 Bot 在 `bot.yaml` 中声明自己的调度策略：

```yaml
scheduling:
  mode: concurrent    # 可选排队（queue）或并发（concurrent）
  max_concurrency: 4 # 最大并发任务数（并发模式）
  queue_size: 100    # 队列容量（排队模式）
```

机器人实例应能同时处理多个 TASK，因此框架需提供任务执行器（Executor），管理并发槽位、任务状态和资源隔离。调度中心（或 TLL 委托模块）会根据目标策略将 TASK 投递给对应执行通道。

### 3.7 Bot 文件夹规范

所有 Bot 统一存放于 `bots/` 目录，每个 Bot 一个子文件夹，结构如下：

```
bots/
└── <bot_name>/
    ├── bot.yaml       # Bot 基础配置（名称、ID、网络、鉴权、工具等）
    └── start.py       # 启动脚本：通过 TLL hook 创建 Bot，并维护网络与监控
```

#### bot.yaml 字段

与 3.3 节一致，包含 `name`、`id`、`network`、`url`、`port`、`auth_key`、`group`、`role`、`functions`、`tools`、`scheduling`、`network_healer`、`permissions`、`fallback`。

#### 启动脚本规范

- 脚本调用 `TLL.bot_factory.request_bot_create(<bot_folder>)` 创建 Bot 实例。
- 创建成功后，脚本负责启动网络监听（如 TCP server / MQTT 订阅）。
- 脚本应定期从 `HookManager.get_recent_events()` 获取事件，输出到控制台或前端大屏，实现监控维护。

示例结构见 `bots/sayi_996/`。

## 四、TLL Protocol 增强设计

基于第一代经验，v2 协议应支持：

- **TASK 承载**：TLLjson 以 TASK 内部字段的形式存在，作为鉴权委托声明，而非独立消息。
- **标准委托字段**：统一委托方、指令、目标方、任务函数、参数、时间、鉴权等。
- **双向鉴权**：携带 token，支持对称/非对称签名。
- **可靠投递**：消息确认（ACK）、超时重试、死信队列（由 TASK 生命周期管理配合）。
- **委托任务**：任务 ID 追踪、结果回调、取消操作（由 TASK 承担）。
- **降级与熔断**：服务不可用时自动切换 fallback。
- **观测数据**：TASK 携带 trace ID，便于链路追踪。
- **检查工具链**：执行前运行额外检查工具列表，确保委托合规。

### 4.1 TLLjson 结构（作为 TASK 内部字段）

TLLjson 是 TASK 中的一个字段，专门负责委托鉴权与声明。其字段如下：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `version` | string | 是 | 协议版本号，保证兼容性 |
| `from` | string | 是 | 委托方标识（用于验证发起者身份） |
| `command` | string | 是 | 委托指令名（如 `file_read`） |
| `to` | string | 是 | 目标方标识（声明预期的接收者） |
| `task_func` | string | 是 | 目标方应执行的函数名（校验指令到函数的映射） |
| `params` | object | 是 | 参数表（检查参数是否符合调用预期） |
| `timestamp` | string | 是 | 委托时间（ISO 8601，防止旧消息重放） |
| `auth` | string/object | 是 | 委托鉴权信息（Token/签名等） |
| `check_tools` | array | 否 | 额外委托检查工具列表（执行前调用的权限/安全验证链） |

注意：TASK 唯一标识、状态、追踪等信息由 TASK 自身管理，不在 TLLjson 中重复。

```json
{
  "task_id": "a1b2c3",
  "trace_id": "trace-xyz",
  "type": "tool",
  "input": {},
  "tlljson": {
    "version": "2.0",
    "from": "agent/sayi_996",
    "command": "file_read",
    "to": "tool/file_operations",
    "task_func": "read_file",
    "params": {"path": "/data/notes.md"},
    "timestamp": "2026-08-13T17:45:00Z",
    "auth": "token-or-signature",
    "check_tools": ["auth_check", "path_whitelist"]
  }
}
```

## 五、记忆系统规则

三层记忆保留但强化：

| 层 | 存储 | 容量 | 写入时机 | 提取方式 |
|----|------|------|----------|----------|
| 短期 | `memory.json` | 80-160条 | 对话实时 | 最近/重要性排序 |
| 摘要 | `summary.md` | 无硬限制 | 短期满或定时 | 分层摘要树 |
| 长期 | `knowledge.json` | 无硬限制 | 重要知识 + 触发词 | 向量 + 关键词混合检索 |

### 规则

1. 短期记忆满时自动摘要，摘要保留关键事实、决策、行动项。
2. 长期记忆必须带触发词，且支持多级分类（领域/标签）。
3. 记忆系统提供统一 API，屏蔽存储细节。
4. 重要信息（用户偏好、系统变更）立即写入长期记忆。

## 六、工具系统规则

- **插件化**：每个工具是一个独立模块，实现统一接口。
- **注册/发现**：启动时扫描插件目录，热注册到 Tool Registry。
- **权限控制**：工具执行前检查权限令牌，敏感操作需交互确认。
- **标准化调用**：输入输出均为 JSON，工具结果附带状态码。
- **可组合**：支持管道式组合（如：搜索→清洗→分析→报告）。

```python
@tool("file_read")
def file_read(path: str, start: int = None, end: int = None) -> dict:
    """Read file content."""
    ...
```

## 七、自适应机制

- **监控指标**：请求延迟、成功率、Token 消耗、错误率。
- **策略库**：预置多种调整策略（如 temperature 动态调整、重试退避）。
- **反馈循环**：使用反馈结果（用户评价、任务完成率）触发策略更新。
- **配置热更新**：运行时修改配置无需重启。

## 八、文本与仓库管理规范

- **统一资源路径**：所有数据通过资源 ID 引用，避免绝对路径散落。
- **仓库化**：`data/assets` 由 Git 管理，支持版本回溯。
- **导出/导入**：提供标准导入导出接口，便于备份与迁移。
- **命名规范**：小写连字符命名（如 `livecodebench-top200.json`）。

## 九、测试与发布

- **单元测试**：核心模块 100% 覆盖关键路径。
- **集成测试**：模拟机器人间通讯、工具委托流程。
- **契约测试**：TLL 协议跨版本兼容验证。
- **灰度发布**：新框架与旧系统并存，逐步切流。

## 十、迁移路线

1. 搭建框架骨架（Config、Runtime、Message Bus、TLL Engine）。
2. 移植并封装现有功能（ToolAgent、WebSearch、PPT/DOCX、PDF）为插件。
3. 建立记忆系统新接口，迁移 `worldsmemory.json` 与摘要。
4. 设计并实现自适应引擎。
5. 编写完整测试与文档。
6. 在真实任务中灰度验证。

## 十一、开放问题

- 是否采用 FastAPI 提供服务化接口？
- LLM 调用抽象层选用 LiteLLM 还是自研？
- TLL 协议是否需要扩展到 WebSocket 传输？
- 是否需要调度中心统一管理多机器人生命周期？

---

*此文档为纲领性草案，细节随实现逐步完善。*

---

## 十二、v0.2.0 变更记录（2026-08-14）

### 12.1 AI 回复协议重构
- LLM 输出统一为 `{reply, commands}` 结构，`commands` 为委托数组。
- 工具规则位于 `tll_protocol/tool_rules.yaml`，要求 LLM 先列出可联系机器人及工具，再输出严格 JSON。
- `llm.py` 的 `plan_task` 已规范化输出。

### 12.2 知识库触发规则
- 上下文出现知识条任一触发词时，将完整知识条（id、content、keywords、updates）加载到上下文。
- 实现在 `knowledge_base.py` 的 `search_by_keyword_hit` 和 `bot.py` 的 `get_knowledge_by_trigger`。

### 12.3 统一 chat 工具
- `tll_protocol/chat_tool.py` 作为 TLL 层统一对话工具，所有机器人共用。
- 机器人收到 `chat` 命令后，调用自身 LLM 生成计划，并通过 `reply` 命令回传。

### 12.4 SaYi_SV 中央调度
- SaYi_SV 由主人扮演，负责创建对话任务并与所有机器人交互，形成委托链。
- `set_sender` 强制为所有非 SV 机器人注入 SaYi_SV 的 peers 和 accept_from。
- Skaye_SV 提供 `grant_permission` 工具（仅对 SaYi_SV 开放）用于动态授权。

### 12.5 LLM 配置统一
- 所有机器人 LLM 服务切换为 DeepSeek：`base_url=https://api.deepseek.com`，`model=deepseek-v4-pro`。
- 各 bot.yaml 的 llm 段统一结构，包含 role_prompt/style_prompt。

---

*更新：SaYi_996，2026-08-14*
