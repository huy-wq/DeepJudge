"""模型供应商的唯一接缝。

流水线与校验只依赖这个协议（文档中的 GradingProvider）；换模型、换供应商都只影响实现类。
"""

from __future__ import annotations

from typing import Protocol


class GradingProvider(Protocol):
    def complete(self, messages: list[dict], *, sample_index: int = 0) -> str:
        """返回模型原始输出文本（期望是 JSON 字符串）。"""
