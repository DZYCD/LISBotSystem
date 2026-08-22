# LISBotSystem

**LIS（World's Memory Library）世忆知识图书馆 · 多智能体协作平台与底层 Agent Harness**

一个从零构建的多智能体（Multi-Agent）协作平台，包含两层：
- **Agent 底层框架（Harness）** —— 单个 LLM Agent 节点的安全推理引擎
- **多节点协作协议（TLL v2）** —— 多个 Agent 节点通过统一委托链模型协作的社区

系统由多个职责分明的机器人组成（EiAr 编程助手族 / SaYi 调度族 / Skaye 监控族），每个机器人是一个独立 LLM 驱动的 Agent 节点，通过 MQTT 消息总线 + 统一委托链模型协作完成复杂任务。

---

## 架构总览

```
┌──────────────────────────────────────────────────────────┐
│                    LISBotSystem                            │
│                                                            │
│  ┌───────────────┐   ┌───────────────┐   ┌──────────────┐  │
│  │  EiAr 族       │   │  SaYi 族       │   │  Skaye 族     │  │
│  │  编程/事务助手  │◄─►│  调度机器人     │◄─►│  监控/牵线     │  │
│  │  (有 LLM)      │   │  (有 LLM)      │   │  (有 LLM)     │  │
│  └───────┬───────┘   └───────┬───────┘   └──────┬───────┘  │
│          │                   │                   │          │
│  ┌───────┴───────────────────┴───────────────────┴───────┐  │
│  │              MQTT 消息总线（统一委托链）                 │  │
│  └───────┬───────────────────────────────────────────────┘  │
│          │                                                   │
│  ┌───────┴─────────────────────────────────────────────────┐ │
│  │              Skaye_SV 中央汇聚节点（注册/握手/存档）     │ │
│  └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

每个机器人内部 = **Agent Harness**（LLM 多步推理引擎 + 安全执行管线 + 工具注册），外部通过 **TLL v2 协议**（统一委托链模型）协作。

---

## 技术栈

| 类别 | 技术 |
|---|---|
| 后端语言 | Python 3.14、asyncio 异步编程 |
| 通信 | MQTT（EMQX broker）、发布/订阅、QoS 2 |
| 协议 | 自研 TLL v2 线协议（JSON over MQTT，加密信封） |
| LLM | DeepSeek 大模型（多步推理、工具调用、分层提示词缓存优化） |
| RAG / 检索 | BGE 中文语义向量、向量余弦相似度、混合检索（向量 + 关键词） |
| 数据 | YAML 配置驱动、JSON 持久化（任务存档、事件流、知识库） |
| 安全 | Windows Job Object 沙箱、动态范围策略、执行前审批 |
| 前端 | 原生 HTML/Canvas 监控大屏（星空节点图、委托链动画） |
| 部署 | 多进程并发（中央节点 + 多 Agent 节点 + embedding 微服务 + 监控服务） |

---

## 核心设计

### 1. 底层 Agent Harness（LLM 的内在）

- **Agent 多步推理引擎**：`while` 循环——LLM 生成文本或 tool-call → 执行工具 → 结果写回会话 → 喂回模型继续推理，直到不再调用工具。支持多步 Tool Use、失败闭环。
- **安全执行管线**：每次工具调用经过「审批（allow/deny）+ 动态沙箱范围策略 + 能力后端执行」三层受保护管线。
- **工具双轨注册**：YAML 声明（`implements` 字段）+ Python 实现，路由到不同后端（`local`/`skill`/`code`/`contact`）。
- **Windows Job Object 沙箱**：治理真实子进程（进程数/内存/超时强制终止），实现安全代码执行。
- **会话记忆（Memory）**：按对话方持久会话 + 记忆压缩沉淀长期知识。
- **分层系统提示词**：利用 LLM prompt caching，稳定层在前、变化层在后，最大化缓存命中。
- **RAG 检索增强**：BGE 语义向量 + 余弦相似度 + 混合检索（向量 + 关键词），供 `memory_query` 工具语义召回记忆。

### 2. 多节点协作协议（LLM 的社区）

- **统一委托链模型**：委托 = 网络工具（`task_create`），本地工具本机直用，委托链 = 嵌套 LLM 循环栈，同步阻塞等回传。
- **TASK id 复用 + 回环检测**：委托链沿途复用 task_id，`_in_flight` 集合 + 挂起检测防止委托回环；禁止自我网络委托。
- **Trace 委托链轨迹**：记录每一跳（`delegate_to_<bot>` / `return_to_<bot>`），完整还原委托链，支持追踪与可视化。
- **中央汇聚（Skaye_SV）**：注册中心（上报注册、ping 握手、任务存档），大屏动态读取。
- **孤岛隔离 + 牵线搭桥**：EiAr 与 SaYi 默认隔离，仅"合作"时由 Skaye 族牵线，实现最小权限、按需协作。
- **监控可视化大屏**：Canvas 星空节点图按组配色，点击节点/任务动态绘制委托链（委托蓝 / 回传绿 / 留档上报橙），展示工具调用日志与对话结果。

---

## 目录结构

```
LIS_v2/
├── lis_harness/           # Agent 底层框架（LLM 内在）
│   ├── agent.py           #   多步推理引擎
│   ├── registry.py        #   工具注册中心 + ToolRuntime
│   ├── skill_loader.py    #   技能加载器
│   ├── session.py         #   会话日志（append-only）
│   ├── security/          #   审批 + 动态范围策略 + 沙箱后端
│   └── adapters/          #   DeepSeek / TLL transport 适配器
├── tll_protocol_v2/       # 多节点协作协议（LLM 社区）
│   ├── node.py            #   节点装配
│   ├── transport.py       #   统一委托链模型
│   ├── start_v2.py        #   节点启动
│   ├── start_all.py       #   一键启动全部
│   ├── sv_tools.py        #   中央汇聚（注册/握手/存档）
│   ├── dashboard_v2.py    #   监控大屏后端
│   ├── memory.py          #   长期记忆 + RAG 接入
│   ├── rag.py             #   RAG 检索引擎
│   ├── embedding_server.py#   BGE embedding 微服务
│   └── dashboard_v2.html  #   监控大屏前端
├── bots/                  # 各机器人配置与技能
│   ├── eiar_001/  eiar_002/   # EiAr 编程/事务助手
│   ├── sayi_996/  sayi_sv/     # SaYi 调度
│   └── skaye_996/ skaye_sv/    # Skaye 监控/中央汇聚
├── tests/                 # harness 单元测试
├── docs/                  # 设计文档（架构/协议/项目经历）
└── .gitignore
```

---

## 快速开始

### 前置条件
- Python 3.14+
- 环境变量：
  - `DEEPSEEK_API_KEY`（DeepSeek 大模型，必需）
  - `BOCHA_API_KEY`（联网搜索，可选）
  - `HF_HOME`（RAG 模型缓存目录，建议指向非 C 盘，如 `D:/models_hf`）

### RAG embedding 微服务（可选，用 ultralytics conda 环境）
```bash
# 复用已有 torch 的 conda 环境启动 BGE embedding 服务
E:\miniconda\envs\ultralytics\python.exe tll_protocol_v2\embedding_server.py 8677
```

### 一键启动全部节点
```bash
cd tll_protocol_v2
python start_all.py
```

### 测试
```bash
# harness 单元测试
python -m unittest discover -s tests
# v2 协议 + 记忆测试
python -m unittest tll_protocol_v2.test_v2 tll_protocol_v2.test_memory
```

---

## 文档

- [Harness 内部架构（LLM 的内在）](docs/harness_architecture.md)
- [TLL v2 通讯协议（LLM 的社区）](docs/protocol.md)
- [项目经历介绍](docs/project_experience.md)

---

## 许可证

© 世忆图书馆 LIS 集群
