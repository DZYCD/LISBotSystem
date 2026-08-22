# LIS-harness 工具清单方案（定稿）

> 状态：方案已确认，待实现
> 目标：让「可对网络开放的工具」清单有唯一权威来源，LISreport/ping 从它上报。

## 一、背景与问题

现有系统的工具信息分散在两处，且不完整：
- `bot.yaml` 的 `tools:` 段：只有 `name` + `access`，**无 description**
- `skills/<name>/tool.yaml` + `tool.py`：有 `description` 和 `handle`
- 硬编码强加载工具（`ping`/`chat`）：在 `bot.py` 里，**不在任何 yaml**

上报（`build_registration_info`）遍历 `self.skills`，而 harness 的 Registry 只有
skills/ 扫描 —— **两个清单不一致**。

## 二、方案：bot.yaml 与 harness 工具 yaml 职责分离

### 2.1 bot.yaml = 机器人"元配置"

```yaml
name: eiar_001
id: agent/eiar_001
network: mqtt
url: broker.emqx.io
port: 1883
auth_key: sk-eiar001
group: EiAr
role: "编程助手"

# peers：关系/鉴权 + 可调用对方工具的名字和参数（A4 委托校验用）
peers:
  agent/skaye_sv:
    auth_key: sk-sv
    tools:
      - name: web_search
        params: { query: "string" }
      - name: summarize

# 指向 harness 工具清单 yaml 的路径（相对 bot.yaml 所在目录）
tool_list: config/tools.yaml
```

- `peers[bot].tools`：数组，每个工具带 `name` + `params`（参数列表）。A4 委托
  校验用它做 command 白名单。
- `tool_list`：相对 bot.yaml 所在目录。

### 2.2 harness 工具清单 yaml = 「当前开放的本机工具」

```yaml
# config/tools.yaml
# 本机开放的工具清单，分公有/私有。每个工具带函数名 + 参数列表。

# 公有：对网络开放（LISreport/ping 上报），也可以自己调用。
# access 支持白名单/黑名单混合两种方法。
public:
  file_read:
    description: "读取文件内容"
    params: { path: "string" }
    access:
      allow: ["*"]
  file_delete:
    description: "删除文件"
    params: { path: "string" }
    access:
      deny: ["agent/sayi_996"]   # 黑名单示例

# 私有：仅供本机内部调用，不上报，带参数和工具名
private:
  _internal_cleanup:
    description: "内部清理任务"
    params: {}
```

- **`public`**：对网络开放，上报 + 自己可调用。access 支持白名单（allow）/
  黑名单（deny）混合。
- **`private`**：仅本机内部调用，不上报。
- **每个工具**：`name`（dict 键）+ `description` + `params`（参数列表）+
  `access`（谁允许调用）。

### 2.3 ping / LISreport：启动时运行时合并（已确认）

`ping` 和 `LISreport` 在 **bot 启动时运行时合并进 public 清单**（内存级，
不物理改写 yaml 文件，避免污染用户手写配置）：

```
bot 启动
  → 读 harness 工具清单 yaml（tool_list）
  → 运行时把 ping / LISreport 合并进 public（内存）
  → 上报时 public 天然包含 ping / LISreport
```

- **不改文件**：只在内存里合并
- **始终包含**：上报时 ping/LISreport 一定在，无论用户是否声明
- **单一来源**：上报 = tool_list 的 public 段 + 运行时强加载

### 2.4 上报逻辑

`ping`/`LISreport` 上报时：
1. 从 `bot.yaml` 读 `tool_list` 路径（相对 bot.yaml）
2. 检索该 yaml 的 `public` 段
3. 运行时合并 ping / LISreport 进 public
4. 生成 `tools`（名字列表）+ `skills`（含 description/access）
5. **skills 结构与 `build_registration_info` 完全一致**（Skaye-SV 接收端不变）

## 三、关键设计决策（已确认）

| 决策 | 理由 |
|---|---|
| `tool_list` 在 bot.yaml，相对路径 | bot.yaml 是元配置，指路；相对所在目录 |
| 工具分 `public`/`private` 两档 | public 上报，private 仅本机；网络用 access 限制 |
| 上报只取 public | 私有工具不暴露 |
| **ping/LISreport 运行时合并，不改文件** | 强加载保证必有，不污染用户 yaml |
| 每个工具带 name + params | 委托方知道怎么调对方工具 |
| skills 结构与 build_registration_info 一致 | Skaye-SV 接收端不变，必须兼容 |
| peers[bot].tools 带 name+params | A4 委托校验的 command 白名单 |
| public 工具也可自己调用 | 白名单/黑名单混合控制 |

## 四、涉及文件

| 文件 | 改动 |
|---|---|
| `bots/eiar_001/bot.yaml` | 加 `tool_list` 字段 + peers 带 tools |
| `config/tools.yaml`（harness） | 改 public/private 结构 + 填真实工具 |
| `lis_harness/report.py`（新增） | 从 bot.yaml 读 tool_list → 生成上报清单 |
| `lis_harness/adapters/tll_transport.py` | ping/LISreport 上报用 report.py |
| `tll_protocol/bot.py` | BotConfig 支持 tool_list；peers 带 tools |

## 五、实现步骤（下一步）

1. 扩展 `BotConfig` 支持 `tool_list`；peers 的 tools 结构
2. 新建 `lis_harness/report.py`：读 bot.yaml → 解析 tool_list yaml → 合并
   ping/LISreport → 生成 tools/skills
3. `tll_transport` 上报用 report.py；A4 校验用 peers[bot].tools
4. 补测试 + 全量回归
