"""文本规范化与原文偏移映射。

判断证据是否成立，要先忽略空白、全半角、大小写差异；但高亮必须回到**原文**的字符位置，
所以规范化时同步记录「规范化下标 → 原文下标」。
"""

from __future__ import annotations

import unicodedata


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """返回 (规范化文本, 每个规范化字符对应的原文下标)。

    NFKC 可能把一个原文字符展开成多个（如 ㍿ → 株式会社），此时这些字符都映射回同一个原文下标。
    """
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        for normalized in unicodedata.normalize("NFKC", ch):
            if normalized.isspace():
                continue
            out.append(normalized.lower())
            index.append(i)
    return "".join(out), index
