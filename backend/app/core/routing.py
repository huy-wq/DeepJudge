"""自动通过 / 转人工的判定。

四项信号：接地率、采样间总分极差、置信度、是否落在边界带。
任何一项不满足就转人工——成绩场景的默认答案应该是"再确认一次"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .aggregate import Aggregate


@dataclass(frozen=True)
class RoutePolicy:
    confidence_threshold: float = 0.75
    max_total_range: float = 1.0
    pass_ratio: float = 0.6
    boundary_margin: float = 1.0
    full_marks_margin: float = 0.5


Route = Literal["auto", "human"]


@dataclass
class RouteDecision:
    route: Route
    risk: float
    reasons: list[str]


def decide(
    aggregate: Aggregate,
    policy: RoutePolicy | None = None,
) -> RouteDecision:
    policy = policy or RoutePolicy()
    reasons: list[str] = []

    if aggregate.grounding_rate < 1.0:
        reasons.append(f"存在未接地判定（接地率 {aggregate.grounding_rate:.2f}）")

    if aggregate.total_range > policy.max_total_range:
        reasons.append(
            f"采样间总分极差 {aggregate.total_range:g} 超过 {policy.max_total_range:g}"
        )

    if policy.confidence_threshold > 0 and aggregate.confidence < policy.confidence_threshold:
        reasons.append(
            f"置信度 {aggregate.confidence:.2f} 低于阈值 {policy.confidence_threshold:.2f}"
        )

    if policy.full_marks_margin > 0 and abs(aggregate.score - aggregate.max_score) <= policy.full_marks_margin:
        reasons.append("总分接近满分边界")

    pass_line = aggregate.max_score * policy.pass_ratio
    if policy.boundary_margin > 0 and abs(aggregate.score - pass_line) <= policy.boundary_margin:
        reasons.append("总分接近及格线边界")

    # 排序用的启发式，没有理论依据：量纲不同，只在同一批次内可比。
    risk = round(
        aggregate.total_range
        + (1 - aggregate.confidence) * aggregate.max_score
        + (1.0 if reasons else 0.0),
        2,
    )
    return RouteDecision(route="auto" if not reasons else "human", risk=risk, reasons=reasons)
