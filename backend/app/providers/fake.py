"""离线假 Provider：按预设脚本返回，不发网络请求。"""

from __future__ import annotations


class CannedProvider:
    # 离线假 provider 没有真实模型可报，审计里就留空
    model_name = ""

    def __init__(self, responses: list[str] | str) -> None:
        self._responses = [responses] if isinstance(responses, str) else list(responses)
        self.calls = 0

    def complete(self, messages: list[dict], *, sample_index: int = 0) -> str:
        if not self._responses:
            raise RuntimeError("CannedProvider 没有可用响应")
        index = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[index]
