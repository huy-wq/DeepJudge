"""多次采样的聚合。

规则（架构 §2.1）：
- 逐采分点取中位数等级，总分用聚合后的等级重算，**不对总分取平均**；
- 离散度按采分点分值与扣分幅度加权，置信度 =（1 − 离散度）× 接地率；
- 扣分项在多数采样中生效时才计入；
- 接地率按"声称命中的判定"合并计算。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import EPS, Rubric, SampleJudgement


@dataclass
class Aggregate:
    levels: dict[str, float]
    score: float
    max_score: float
    dispersion: float
    confidence: float
    sample_totals: list[float] = field(default_factory=list)
    total_range: float = 0.0
    grounding_rate: float = 1.0
    applied_deductions: list[str] = field(default_factory=list)


def _median_level(values: list[float], allowed: tuple[float, ...]) -> float:
    """取下中位数；若落在非法等级上，贴到最近的合法等级。"""
    ordered = sorted(values)
    lower = ordered[(len(ordered) - 1) // 2]
    if any(abs(lower - candidate) < EPS for candidate in allowed):
        return lower
    return min(allowed, key=lambda candidate: abs(candidate - lower))


def sample_score(rubric: Rubric, sample: SampleJudgement) -> float:
    """单次采样的分数：只统计已接地的扣分项。"""
    total = sum(
        rubric.item(judgement.item_id).score_for(judgement.level)
        for judgement in sample.items
        if judgement.item_id in {item.id for item in rubric.items}
    )
    applied = {
        deduction.id
        for deduction in sample.deductions
        if deduction.hit and deduction.grounded
    }
    total += sum(item.effective() for item in rubric.deductions if item.id in applied)
    return round(max(0.0, min(rubric.total_score, total)), 2)


def _deduction_is_majority(samples: list[SampleJudgement], deduction_id: str) -> bool:
    hits = sum(
        1
        for sample in samples
        if any(
            deduction.id == deduction_id and deduction.hit and deduction.grounded
            for deduction in sample.deductions
        )
    )
    return hits * 2 > len(samples)


def aggregate(rubric: Rubric, samples: list[SampleJudgement]) -> Aggregate:
    if not samples:
        raise ValueError("至少需要一次采样结果")

    levels: dict[str, float] = {}
    spreads: dict[str, float] = {}
    for item in rubric.items:
        values = [sample.levels().get(item.id, 0.0) for sample in samples]
        levels[item.id] = _median_level(values, item.allowed_levels)

        allowed = item.allowed_levels
        span = max(allowed) - min(allowed)
        spreads[item.id] = (max(values) - min(values)) / span if span > EPS else 0.0

    score = sum(rubric.item(item_id).score_for(level) for item_id, level in levels.items())
    applied_deductions = [
        deduction.id
        for deduction in rubric.deductions
        if _deduction_is_majority(samples, deduction.id)
    ]
    score += sum(
        deduction.effective()
        for deduction in rubric.deductions
        if deduction.id in applied_deductions
    )
    score = round(max(0.0, min(rubric.total_score, score)), 2)

    totals = [sample_score(rubric, sample) for sample in samples]
    claims = [judgement.grounded for sample in samples for judgement in sample.items if judgement.hit]
    claims += [deduction.grounded for sample in samples for deduction in sample.deductions if deduction.hit]
    grounding_rate = (sum(1 for ok in claims if ok) / len(claims)) if claims else 1.0

    # 扣分项没有独立权重，用扣分幅度的绝对值折算；整体截断到 1，避免越界。
    spread_terms = sum(spreads[item.id] * item.weight for item in rubric.items)
    for deduction in rubric.deductions:
        flags = [
            1.0
            if any(
                entry.id == deduction.id and entry.hit and entry.grounded
                for entry in sample.deductions
            )
            else 0.0
            for sample in samples
        ]
        spread_terms += (max(flags) - min(flags)) * abs(deduction.effective())

    total_weight = sum(item.weight for item in rubric.items) or 1.0
    dispersion = min(1.0, spread_terms / total_weight)

    return Aggregate(
        levels=levels,
        score=score,
        max_score=rubric.total_score,
        dispersion=round(dispersion, 4),
        confidence=round(max(0.0, min(1.0, (1.0 - dispersion) * grounding_rate)), 4),
        sample_totals=totals,
        total_range=round(max(totals) - min(totals), 2),
        grounding_rate=round(grounding_rate, 4),
        applied_deductions=applied_deductions,
    )
