"""模型供应商的唯一接缝。

流水线与校验只依赖 GradingProvider 协议；换模型、换供应商都只影响实现类。
"""

from __future__ import annotations

from typing import Protocol


class ProviderError(Exception):
    """供应商调用失败。

    code 供上层决策：retryable 为真时值得重试（限流、超时、5xx），
    为假时重试没有意义（鉴权失败、余额不足、参数错误）。
    """

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"[{code}] {message}")


class GradingProvider(Protocol):
    def complete(self, messages: list[dict], *, sample_index: int = 0) -> str:
        """返回模型原始输出文本（期望是 JSON 字符串）。"""
