"""文本规范化与原文偏移映射。

判断证据是否成立，要先忽略空白、全半角、大小写差异；但高亮必须回到**原文**的字符位置，
所以规范化时同步记录「规范化下标 → 原文下标」。
"""

from __future__ import annotations

import hashlib
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


def hash_normalized(normalized: str) -> str:
    """对**已经规范化过**的文本取指纹。

    调用方常常刚刚做过规范化（导入时要存 normalized_text），再调 `content_hash`
    等于同一段文本规范化两遍。
    """
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    """查重用的内容指纹：先规范化（去空白、全半角、大小写），再取 sha256。

    口径是"看起来一样就算重复"：全角逗号与半角逗号、多一个空格，都算同一份答卷。
    """
    return hash_normalized(normalize_with_map(text)[0])
