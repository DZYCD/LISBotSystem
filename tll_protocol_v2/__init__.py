"""TLL Protocol v2 —— 基于统一模型的整体重写。

核心理念：所有网络活动都是委托链；网络委托 = 调用网络工具（工具名 + 参数字典）；
自己用自己工具 = 本地请求。harness Agent 循环成为执行核心，本地工具和网络委托
统一为 tool_call。

v2 复用 LIS-harness 的核心组件（Agent/Session/Registry/沙箱/TLLTransport），
但用真实 MQTT 传输，并围绕「同步阻塞等回传」的桥接机制设计。
"""

__version__ = "0.2.0"
