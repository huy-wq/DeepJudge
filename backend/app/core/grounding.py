"""证据接地：模型引用的原文片段必须真的能在答卷里找到。

命中分两级：
1. 规范化后的精确子串；
2. 容错匹配——只在**以最长匹配块为中心的局部窗口**内评估，避免把散落各处的词
   拼成"命中"，同时允许短证据有 1 个字符的偏差。

定位不到就判未接地：该采分点按 0 分处理，扣分项不生效。
"""

from __future__ import annotations

import difflib

from .normalize import normalize_with_map
from .types import DeductionJudgement, ItemJudgement

DEFAULT_FUZZY_THRESHOLD = 0.9


def locate_evidence(
    raw: str,
    evidence: str,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> tuple[int, int] | None:
    """在原文中定位证据，返回原文的 [start, end)；定位不到返回 None。"""
    if not raw or not evidence or not evidence.strip():
        return None

    norm_raw, index = normalize_with_map(raw)
    norm_evidence, _ = normalize_with_map(evidence)
    if not norm_raw or not norm_evidence:
        return None

    position = norm_raw.find(norm_evidence)
    if position >= 0:
        return (index[position], index[position + len(norm_evidence) - 1] + 1)

    length = len(norm_evidence)
    blocks = [
        block
        for block in difflib.SequenceMatcher(
            None, norm_raw, norm_evidence, autojunk=False
        ).get_matching_blocks()
        if block.size
    ]
    if not blocks:
        return None

    # 只围绕最长匹配块开一个局部窗口，避免"整卷范围内拼凑命中"。
    anchor = max(blocks, key=lambda block: block.size)
    slack = max(2, length // 5)
    window_start = max(0, anchor.a - anchor.b - slack)
    window_end = min(len(norm_raw), window_start + length + 2 * slack)
    window = norm_raw[window_start:window_end]

    local_blocks = [
        block
        for block in difflib.SequenceMatcher(
            None, window, norm_evidence, autojunk=False
        ).get_matching_blocks()
        if block.size
    ]
    if not local_blocks:
        return None

    matched = sum(block.size for block in local_blocks)
    # 短证据允许 1 个字符的偏差；长证据按阈值换算。
    max_errors = max(1, int(length * (1 - fuzzy_threshold)))
    if length - matched > max_errors:
        return None

    first = local_blocks[0]
    last = local_blocks[-1]
    start = window_start + first.a
    end = window_start + last.a + last.size
    return (index[start], index[end - 1] + 1)


def _ground(judgement, raw_answer: str, fuzzy_threshold: float) -> None:
    if not judgement.hit:
        judgement.grounded = True
        return

    span = locate_evidence(raw_answer, judgement.evidence, fuzzy_threshold)
    if span is None:
        judgement.mark_ungrounded()
    else:
        judgement.mark_grounded(span)


def apply_grounding(
    items: list[ItemJudgement],
    deductions: list[DeductionJudgement],
    raw_answer: str,
) -> None:
    """就地标记接地结果。

    hit 保留模型的原始声称；未接地的采分点 level 置 0，未接地的扣分项不生效。
    这样既不影响计分，也不丢掉"模型声称过什么"的审计信息。
    """
    for judgement in items:
        _ground(judgement, raw_answer, DEFAULT_FUZZY_THRESHOLD)
    for deduction in deductions:
        _ground(deduction, raw_answer, DEFAULT_FUZZY_THRESHOLD)
