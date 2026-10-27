"""从模型输出里取出 JSON 对象。

模型不总是老实输出纯 JSON：常见的是包一层 ```json 围栏，或者前后多说一句
"好的，以下是判定结果"。这一层只管"文本 → dict"的两件事，不再往下解释内容。

两个调用方（评分流水线、标准生成）都需要它，而 `core/` 不允许 import `pipeline`，
所以放在 core；各自在外层用自己的错误类型表达"这一步失败了"。
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class PayloadError(ValueError):
    """模型输出不能当成 JSON 对象用。

    `code` 是稳定标识：`INVALID_JSON`（不是 JSON）/ `NOT_OBJECT`（是 JSON 但不是对象）。
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


def extract_payload(text: str) -> dict[str, Any]:
    """剥掉代码围栏后解析 JSON，并要求结果是一个对象。"""
    text = (text or "").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PayloadError("INVALID_JSON", f"模型输出不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise PayloadError("NOT_OBJECT", "模型输出必须是 JSON 对象")
    return payload
