"""文本规范化与偏移映射。

证据接地（grounding）要求：模型给出的 evidence 必须能回到**原始答卷**中定位。
规范化会改变字符数（全角转半角、去空白），所以必须维护「规范化下标 -> 原文下标」
的映射，才能在前端正确高亮原文。
"""

from __future__ import annotations

import unicodedata


def normalize_text(text: str) -> str:
    """NFKC 规范化 + 去空白 + 小写。用于匹配，不用于展示。"""
    return "".join(
        ch.lower()
        for ch in unicodedata.normalize("NFKC", text)
        if not ch.isspace()
    )


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """返回 (规范化文本, 每个规范化字符对应的原文下标)。

    NFKC 可能把一个原文字符展开成多个（如 ㍿ -> 株式会社），此时这些字符都映射
    回同一个原文下标。
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
