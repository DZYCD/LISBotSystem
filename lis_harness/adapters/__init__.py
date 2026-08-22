"""LLM 适配器包：把 harness 的模型接口接到真实供应商。"""

from ..llm import LlmClient, LlmResult, MockLlmClient
from .deepseek import DeepSeekClient, DeepSeekError
from .tll_transport import TLLTask, TLLTransport, TLLTransportConfig

__all__ = [
    "DeepSeekClient",
    "DeepSeekError",
    "LlmClient",
    "LlmResult",
    "MockLlmClient",
    "TLLTask",
    "TLLTransport",
    "TLLTransportConfig",
]
