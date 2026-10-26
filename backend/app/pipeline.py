"""单次评分流水线：组装提示词 → 调用 → 解析 → 服务端算分。

总分一律由这里计算；模型输出中的任何分数、满分字段都不采信。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .core.prompt import build_messages
from .core.types import EPS, DeductionJudgement, ItemJudgement, Rubric, SampleJudgement
from .providers.base import GradingProvider

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class GradingError(Exception):
    """评分失败：输出不可用，而不是"得了 0 分"。

    code 便于上层区分处理：INVALID_JSON / NOT_OBJECT / MISSING_ITEM / INVALID_LEVEL。
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


def extract_payload(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GradingError("INVALID_JSON", f"模型输出不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise GradingError("NOT_OBJECT", "模型输出必须是 JSON 对象")
    return payload


def _index_by_id(entries: Any) -> dict[str, dict]:
    """把模型返回的列表按 id 建索引；非对象或缺 id 的条目直接丢弃。"""
    indexed: dict[str, dict] = {}
    for raw in entries or []:
        if isinstance(raw, dict) and raw.get("id") is not None:
            indexed[str(raw["id"])] = raw
    return indexed


def _parse_items(rubric: Rubric, payload: dict[str, Any]) -> list[ItemJudgement]:
    entries = _index_by_id(payload.get("items"))

    judgements: list[ItemJudgement] = []
    for item in rubric.items:
        entry = entries.get(item.id)
        if entry is None:
            raise GradingError("MISSING_ITEM", f"模型漏掉了采分点 {item.id} 的判定")

        try:
            level = float(entry["level"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GradingError("INVALID_LEVEL", f"采分点 {item.id} 缺少合法等级") from exc

        if not any(abs(level - allowed) < EPS for allowed in item.allowed_levels):
            raise GradingError(
                "INVALID_LEVEL",
                f"采分点 {item.id} 的等级 {level:g} 不在允许值 {list(item.allowed_levels)} 中",
            )

        judgements.append(
            ItemJudgement(
                item_id=item.id,
                level=level,
                hit=bool(entry.get("hit", False)),
                evidence=str(entry.get("evidence", "") or ""),
                reason=str(entry.get("reason", "") or ""),
            )
        )
    return judgements


def _parse_deductions(
    rubric: Rubric, payload: dict[str, Any]
) -> list[DeductionJudgement]:
    # 扣分项是"命中才出现"的负面证据：模型未提及即视为未触发。
    entries = _index_by_id(payload.get("deductions"))
    return [
        DeductionJudgement(
            id=deduction.id,
            hit=bool(entries.get(deduction.id, {}).get("hit", False)),
            evidence=str(entries.get(deduction.id, {}).get("evidence", "") or ""),
        )
        for deduction in rubric.deductions
    ]


def grade(
    rubric: Rubric,
    answer: str,
    provider: GradingProvider,
    *,
    sample_index: int = 0,
) -> SampleJudgement:
    raw = provider.complete(build_messages(rubric, answer), sample_index=sample_index)
    payload = extract_payload(raw)

    items = _parse_items(rubric, payload)
    deductions = _parse_deductions(rubric, payload)

    total = sum(rubric.item(ij.item_id).score_for(ij.level) for ij in items)
    hit_ids = {dj.id for dj in deductions if dj.hit}
    total += sum(d.effective() for d in rubric.deductions if d.id in hit_ids)

    return SampleJudgement(
        question_id=rubric.question_id,
        items=items,
        deductions=deductions,
        comment=str(payload.get("comment", "") or ""),
        score=round(max(0.0, min(rubric.total_score, total)), 2),
        max_score=rubric.total_score,
    )
