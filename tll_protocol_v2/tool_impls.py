"""工具实现注册表：把 tool_list yaml 声明的工具名映射到对应后端实现。

双轨原则：工具注册 = Python 实现 + yaml 声明。yaml 只声明"开放哪些工具"，
Python 侧提供实现。本模块是"工具名 → 后端工厂"的映射，供 node 从 yaml 加载时
实例化正确的后端。

与 harness PluginLoader 的区别：这里面向「机器人开放给网络的本地工具」，
实现来自 v2 自己的模块（contact_tools / local 文件后端 / skill）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ToolImplRegistry:
    """工具实现注册表：name -> 后端工厂回调。"""

    def __init__(self) -> None:
        self._factories: Dict[str, callable] = {}

    def register(self, name: str, factory: callable) -> None:
        self._factories[name] = factory

    def has(self, name: str) -> bool:
        return name in self._factories

    def create_backend(self, name: str, **ctx):
        """为工具创建后端实例。ctx 提供构造所需的上下文（如 bot_yaml_path）。"""
        factory = self._factories.get(name)
        if factory is None:
            return None
        return factory(**ctx)


# 模块级默认注册表（可被 node 扩展）
_registry = ToolImplRegistry()


def get_registry() -> ToolImplRegistry:
    return _registry
