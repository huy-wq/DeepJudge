"""单次评分流水线：组装提示词 → 调用 → 解析 → 服务端算分。

总分一律由这里计算；模型输出中的任何分数、满分字段都不采信。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .core.aggregate import Aggregate, aggregate, sample_score
from .core.grounding import apply_grounding
from .core.json_extract import PayloadError, extract_payload
from .core.prompt import build_messages
from .core.routing import RouteDecision, RoutePolicy, decide
from .core.types import EPS, DeductionJudgement, ItemJudgement, Rubric, SampleJudgement
from .providers.base import GradingProvider


class GradingError(Exception):
    """评分失败：输出不可用，而不是"得了 0 分"。

    code 便于上层区分处理：INVALID_JSON / NOT_OBJECT / MISSING_ITEM / INVALID_LEVEL。
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


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
    check_grounding: bool = True,
) -> SampleJudgement:
    """跑一次评分。`check_grounding=False` 只给对照实验的 B2 用。

    不校验证据时把判定全部标成 grounded：否则扣分项永远不生效、
    置信度被接地率拉到 0，B2 与 B3 的差异就混进了别的变量。
    """
    raw = provider.complete(build_messages(rubric, answer), sample_index=sample_index)
    try:
        payload = extract_payload(raw)
    except PayloadError as exc:
        # 换成流水线自己的错误类型：调用方只需要认 GradingError 一种
        raise GradingError(exc.code, exc.message) from exc

    items = _parse_items(rubric, payload)
    deductions = _parse_deductions(rubric, payload)
    if check_grounding:
        apply_grounding(items, deductions, answer)
    else:
        for judgement in items:
            judgement.grounded = True
        for judgement in deductions:
            judgement.grounded = True

    sample = SampleJudgement(
        question_id=rubric.question_id,
        items=items,
        deductions=deductions,
        comment=str(payload.get("comment", "") or ""),
        max_score=rubric.total_score,
    )
    sample.score = sample_score(rubric, sample)
    return sample


@dataclass
class Evaluation:
    samples: list[SampleJudgement]
    aggregate: Aggregate
    route: RouteDecision


def evaluate(
    rubric: Rubric,
    answer: str,
    provider: GradingProvider,
    *,
    samples: int = 3,
    policy: RoutePolicy | None = None,
    check_grounding: bool = True,
) -> Evaluation:
    """采样若干次后聚合，并给出自动通过 / 转人工的结论。

    `check_grounding=False` 只给对照实验的 B2 用；正常评分永远走默认值。
    """
    if samples < 1:
        raise ValueError("采样次数至少为 1")

    results = [
        grade(
            rubric,
            answer,
            provider,
            sample_index=index,
            check_grounding=check_grounding,
        )
        for index in range(samples)
    ]
    combined = aggregate(rubric, results)
    decision = decide(combined, policy)
    return Evaluation(samples=results, aggregate=combined, route=decision)
