# LIS-harness 整体 Review（最终版）

> 方法：手动审查 + 独立批判性审查子任务（静态通读 + 运行时探针实测复现）。
> 关键结论均已实测，非纸面推测。审查未修改源码。

## 整改进度

- ✅ **沙箱整改**（S1/S2/S4/S5）：命令需 DANGER 档、read 走范围检查、命令输出捕获
- ✅ **失败闭环整改**（D4/D5/Q2）：工具失败转 tool/result 喂给模型、AgentOptions 隔离
- ✅ **TLL 结构对齐**（TLLTask → 真实 Task/TLLjson）：已用真实 Task.from_dict 验证可解析
- ✅ **A4 能力校验**：委托 command 现在校验 peer 声明能力（tools 白名单）
- ⬜ **A1 统一注册机制**：仍有三套（PluginLoader/SkillLoader/Registry 直连），已明确职责分工但未物理合并
- ⬜ **热重载事务性**（H1）：重载失败无回滚，工具可能消失
- ⬜ **D2 dump 丢 time / D3 无版本校验**

## 严重程度标记：🔴高 / 🟡中 / 🟢低

---

## 一、最高优先级（上生产整合进 LIS v2 前必须修）

### 🔴 S1 命令路径完全绕过沙箱（最大安全洞，已实测）
`JobObjectShell._run_command`（jobshell.py:111-136）**从不检查 policy**。
- README 宣称"Job 管资源 + SandboxPolicy 管路径范围"
- 但命令形式只有 Job 资源治理，无任何路径/模式治理
- **已实测**：READ_ONLY 模式下执行 `python -c "写 TEMP 文件"` 返回 ok=True 且文件确实落盘
- 即"只读"档位下可执行任意 shell 命令

### 🔴 S2 read 操作无任何策略检查（已实测）
`_handle_read`（jobshell.py:100-107）和 InProcessShell 的 read 在任何模式下都能读任意路径。
- **已实测**：READ_ONLY + workspace_root=None 下读 `C:/Windows/win.ini` 成功

### 🔴 H1 重载非事务性，工具可能永久消失
`reload_tool`：先 unload 旧注册 → reload 模块 → 重建。若新 `create()` 抛错，watch 已 pop 且旧注册已卸载，**无回滚**，工具永久消失。

### 🔴 A1 三套工具注册机制并存（与"单一来源"矛盾）
1. `PluginLoader`：config/tools.yaml + create 工厂
2. `SkillLoader`：skills/*/tool.yaml + handle 函数
3. `Registry.register_tool` 直连
两种插件格式互不兼容，两份 YAML schema 字段不同。实际是**三个来源**。

### 🔴 A4 委托治理不校验 command 能力
白名单只校验目标机器人，**从不校验 command 是否在该 peer 声明的能力内**。peers 的 tools 列表是死数据。

---

## 二、安全层面（除 S1/S2 外）

### 🟡 S3 默认审批=放行，审批机制 opt-in
`ExecutionPipeline.default_verdict = ALLOW`，demo 和 proto 都没注册 pre-execute 监听器——审批服务在"生产路径"从未被调用。且审批后 escalation 不经 `can_upgrade_to` 校验，可请求任意档位（含降级）。

### 🟡 S4 TOCTOU + 检查与落盘目标不一致
`allows_write_to` 对 `path.resolve()` 判断，`_handle_write` 却用未 resolve 的原始路径写。检查与写入之间可被换符号链接；相对路径按 CWD 而非 workspace_root。

### 🟡 S5 命令输出从不捕获（已实测）
winjob spawn 用默认 STARTUPINFOW（无管道），子进程输出继承父控制台；`echo HELLO_CAPTURE` 的输出不在 result 里，**模型拿不到任何命令输出**。

### 🟡 S6 wait() 退出码不可靠
进程已消失时 OpenProcess 失败返回 exit_code=0，非零退出码被误报成功；spawn 关闭句柄后 PID 可能被回收。

### 🟡 S7 资源治理是"每调用一个 Job"
每次 execute 新建 Job，max_active_processes 只约束单次调用，会话级总量无治理；`max_process_time_ms` 是死配置（未暴露）。

### 🟡 S9 SkillBackend 忽略 SandboxPolicy
skill 工具天然绕过路径沙箱且无文档说明。

### 🟢 S8 审批不向用户展示升级范围
prompt 签名没有 requested_mode，用户批准时看不到要升到什么档。

---

## 三、数据一致性（session.py 为核心）

### 🟡 D1 content 单对象/列表契约三处三套假设（已实测）
tool/result 写单个 ToolResultBlock，user/assistant 写列表；`derive_messages` 无条件再包一层——若已是列表则双重嵌套。已实测 `{'content':[ToolResultBlock]}` 投影后 `msg.content[0]` 是 list。

### 🟡 D2 dump 丢失 time
`_event_to_dict` 只输出 type/data/seq，restore 后 time=0.0。回放后时序信息全丢。

### 🟡 D3 restore 无版本/校验
dump 无 schema 版本；未知 block type 抛 TypeError 中断，产生半恢复状态。

### 🟡 D4 异常路径破坏日志闭环
未知工具（KeyError）/后端异常/坏 JSON 冒泡到 run() 的 except，该 tool/call **没有对应 tool/result**——模型永远看不到失败原因。原始异常文本直接拼进 final_text（可能泄露内部路径）。

### 🟡 D5 坏 JSON 参数静默变 {}
`except json.JSONDecodeError: args = {}`，模型坏参数被当空参数执行。

### 🟢 D6 restore 后 seq 非连续时 `session.seq = len(_log)` 可能与已有 seq 冲突

---

## 四、架构（除 A1/A4 外）

### 🟡 A2 封装被击穿
agent.py:144 直接访问 `self.tool_runtime._registry`；proto 访问 `tll._config.peers`。ToolRuntime 无公开 list_tools 接口。

### 🟡 A3 "会话日志是唯一真相源"在实践中有多个平行内存镜像
TLLTransport.sent_tasks、哨兵计数器等。demo 验证读哨兵而非日志；哨兵状态无法从日志重建，违反"模型可见⟺已记录"的推导方向。

### 🟡 A5 loader 的"整体卸载"是假能力
`_load_backend` 里 register_backend 的 disposer 被直接丢弃，后端无法随插件卸载。

### 🟢 A6 哨兵 mount 不幂等
重复 mount 重复订阅/注册，无守卫。

### 🟢 A7 requested_mode 整条链路是死代码
ToolRuntime 构造请求从不传 requested_mode，没有任何工具设置它。

---

## 五、代码质量

### 🟡 Q1 三处静默吞异常且无日志
events.py emit、skill_loader scan、_load_handler 都吞异常，坏 skill 无声消失。

### 🟡 Q2 AgentOptions() 共享默认实例（已实测）
默认参数在函数定义时求值一次，所有未传 options 的 Agent 共享同一对象。`a1.options is a2.options == True`。test_agent.py 改一个影响全部。应改 `field(default_factory=...)`。

### 🟡 Q3 测试直接改写真实源码
test_loader.py 改写 bash_tool.py，并行/崩溃会弄脏源码树。

### 🟢 Q4 死代码
active_process_count、can_upgrade_to、mount/shutdown、max_process_time_ms 均无有效调用方。

### 🟢 Q5 demo/proto 硬编码绝对 Windows 路径，不可移植

### 🟢 Q6 EventBus disposer 按值 list.remove，同一 handler 订阅两次时先卸载的删错条目

### 🟢 Q7 事件词汇名不副实（docstring 声称含 assistant/message，实际只发 tool/call、tool/result）

---

## 六、热重载（除 H1 外）

### 🟡 H2 importlib.reload 的陈旧引用
reload 只更新 loader 持有的 module；其他模块持有旧引用仍指旧实现。热重载只对 loader 生效。

### 🟡 H3 mtime 粒度与内容不变问题
FAT32 2s 粒度文件系统上同 tick 内两次写入不触发；只看 mtime 不看内容哈希。

### 🟡 H4 后端与配置声明不参与热重载
_load_backend 不建 watch，后端文件或 timeout_ms 改动永不生效，需重启。

---

## 七、结论：上生产最该先修的 3 件事

1. **封死 JobObjectShell 的命令/read 沙箱绕过（S1+S2）**——当前"沙箱"在命令路径上形同虚设，这是多机器人系统里被委托方恶意利用的入口。命令形式在非 DANGER_FULL_ACCESS 档下拒绝或强制降权；read 走范围判断；显式声明"命令不受路径治理"现状。
2. **修 agent 循环的失败闭环与共享默认实例（D4+D5+Q2）**——未知工具/异常/坏 JSON 必须转成带错误信息的 tool/result 写回日志喂给模型（模型可自纠、日志完整闭环），而非整轮崩溃；AgentOptions 改 default_factory。
3. **统一工具注册机制，兑现"单一来源"（A1+A3/A4）**——三套收敛为一套（建议 skill_loader 的 tool.yaml+handle 为唯一格式）；TLL 与 harness 由同一份声明生成并校验 peer 的 command 白名单；dump 补 time 与 schema 版本。
