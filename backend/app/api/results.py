"""复核接口：待复核队列、逐条详情、改分与批量通过。

三件事分别对应教师的三步动作：先看**哪几份**最可疑（队列按 risk 排序），
再看**某一分为何成立**（逐条判定 + 证据偏移 + 采样分歧），
最后**定稿**（采纳或改分，写一条复核记录）。

定稿后不再改写结果：`aggregated_results` 里的原分、标准快照、逐条判定全都不动，
复核只追加一行 `reviews`。这样"当时模型给了多少"和"人工改成多少"两份证据都在。
"""

from __future__ import annotations

from typing import Literal

from fastapi import Depends, FastAPI, Query

from ..core.types import EPS
from ..store import ResultNotFound, ReviewRecord, Store, StoredEvaluation
from .deps import require_admin, require_store
from .schemas import BatchAcceptRequest, ReviewRequest


def _span(span: tuple[int, int] | None) -> dict | None:
    return None if span is None else {"start": span[0], "end": span[1]}


def _review_payload(record: ReviewRecord) -> dict:
    return {
        "review_id": record.review_id,
        "aggregate_id": record.aggregate_id,
        "reviewer_id": record.reviewer_id,
        "final_score": record.final_score,
        "decision": record.decision,
        "comment": record.comment,
        "created_at": record.created_at,
    }


def _sample_rows(loaded: StoredEvaluation, judgements: list, *, with_level: bool) -> list[dict]:
    """把"每个采样各自怎么判"摊平成一行行。采分点与扣分项共用这段。"""
    rows = []
    for run_id, judgement in zip(loaded.run_ids, judgements):
        row = {
            "run_id": run_id,
            "hit": bool(judgement is not None and judgement.hit),
            "grounded": bool(judgement is not None and judgement.grounded),
            "evidence": judgement.evidence if judgement is not None else "",
            "span": _span(judgement.span) if judgement is not None else None,
        }
        if with_level:
            row["level"] = judgement.level if judgement is not None else None
        rows.append(row)
    return rows


def _item_rows(loaded: StoredEvaluation) -> list[dict]:
    """把"采分点定义 + 逐采样判定 + 聚合等级"拼成详情页的一行。

    证据优先取"等级等于中位数且已接地"的那次采样：它既解释了最终分，
    也能直接在原文上高亮。都不满足时退回第一条，并让 `grounded=false` 自己说话。
    """
    samples = loaded.evaluation.samples
    aggregate = loaded.evaluation.aggregate
    rows = []
    for item in loaded.rubric.items:
        judgements = [
            next((judgement for judgement in sample.items if judgement.item_id == item.id), None)
            for sample in samples
        ]
        present = [judgement for judgement in judgements if judgement is not None]
        # 用中括号而不是 .get(…, 0.0)：等级缺失说明数据被绕过写入流程改坏了，
        # 凭空补 0 会静默造出一个"评了 0 分"的假结论。
        median = aggregate.levels[item.id]
        chosen = next(
            (
                judgement
                for judgement in present
                if judgement.grounded and abs(judgement.level - median) < EPS
            ),
            None,
        )
        if chosen is None:
            chosen = next(
                (judgement for judgement in present if judgement.grounded),
                present[0] if present else None,
            )
        rows.append(
            {
                "id": item.id,
                "desc": item.desc,
                "weight": item.weight,
                "levels": [
                    {"level": level.level, "score": level.score, "criterion": level.criterion}
                    for level in item.levels
                ],
                "level": median,
                "score": item.score_for(median),
                "sample_levels": [
                    judgement.level if judgement is not None else None
                    for judgement in judgements
                ],
                "disagree": len(
                    {round(judgement.level, 6) for judgement in present}
                )
                > 1,
                "hit": any(judgement.hit for judgement in present),
                "grounded": bool(present) and all(judgement.grounded for judgement in present),
                "evidence": chosen.evidence if chosen else "",
                "span": _span(chosen.span) if chosen else None,
                "reason": chosen.reason if chosen else "",
                "samples": _sample_rows(loaded, judgements, with_level=True),
            }
        )
    return rows


def _deduction_rows(loaded: StoredEvaluation) -> list[dict]:
    samples = loaded.evaluation.samples
    applied = set(loaded.evaluation.aggregate.applied_deductions)
    rows = []
    for deduction in loaded.rubric.deductions:
        judgements = [
            next((entry for entry in sample.deductions if entry.id == deduction.id), None)
            for sample in samples
        ]
        present = [judgement for judgement in judgements if judgement is not None]
        hits = [judgement for judgement in present if judgement.hit]
        chosen = next((judgement for judgement in hits if judgement.grounded), None)
        if chosen is None:
            chosen = hits[0] if hits else (present[0] if present else None)
        rows.append(
            {
                "id": deduction.id,
                "desc": deduction.desc,
                "penalty": deduction.penalty,
                "cap": deduction.cap,
                "applied": deduction.id in applied,
                "hit_samples": len(hits),
                "evidence": chosen.evidence if chosen else "",
                "span": _span(chosen.span) if chosen else None,
                "reason": chosen.reason if chosen else "",
                "samples": _sample_rows(loaded, judgements, with_level=False),
            }
        )
    return rows


def _detail_payload(
    loaded: StoredEvaluation,
    review: ReviewRecord | None,
    *,
    latest_aggregate_id: int | None,
) -> dict:
    combined = loaded.evaluation.aggregate
    route = loaded.evaluation.route
    return {
        "aggregate_id": loaded.aggregate_id,
        # 旧链接打开的是"某一次结论"；它不是当前结论时必须让前端能提示，否则
        # 教师会以为这就是成绩。定稿接口对过期结论返回 409，详情页先给出同样的信号。
        "current": latest_aggregate_id == loaded.aggregate_id,
        "superseded_by": (
            None if latest_aggregate_id == loaded.aggregate_id else latest_aggregate_id
        ),
        # 答卷原文与题号：前端要按 span 在原文上高亮，所以这里必须给全文
        "answer": {
            "answer_id": loaded.answer_id,
            "pseudo_id": loaded.student_pseudo_id,
            "text": loaded.answer_text,
            "truncated": loaded.truncated,
        },
        # 当时的标准（不是"现在的最新版"）：标准后来被改，这里也不会变
        "rubric": {
            "question_id": loaded.rubric.question_id,
            "stem": loaded.rubric.stem,
            "reference_answer": loaded.rubric.reference_answer,
            "total_score": loaded.rubric.total_score,
        },
        "score": {
            "raw": combined.score,
            "final": review.final_score if review is not None else None,
            "max": combined.max_score,
            "route": route.route,
            "risk": route.risk,
            "reasons": route.reasons,
            "dispersion": combined.dispersion,
            "total_range": combined.total_range,
            "confidence": combined.confidence,
            "grounding_rate": combined.grounding_rate,
            "sample_totals": combined.sample_totals,
            "applied_deductions": combined.applied_deductions,
        },
        "items": _item_rows(loaded),
        "deductions": _deduction_rows(loaded),
        "samples": [
            {"run_id": run_id, "score": sample.score, "levels": sample.levels()}
            for run_id, sample in zip(loaded.run_ids, loaded.evaluation.samples)
        ],
        "review": _review_payload(review) if review is not None else None,
    }


def register(app: FastAPI) -> None:
    @app.get("/api/results", dependencies=[Depends(require_admin)])
    def list_results(
        route: Literal["auto", "human"] | None = "human",
        pending: bool = True,
        question_code: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        store: Store = Depends(require_store),
    ) -> dict:
        rows = store.list_results(
            route=route,
            pending=pending,
            question_code=question_code,
            limit=limit,
            offset=offset,
        )
        return {
            "route": route,
            "pending": pending,
            # total 是符合条件的总数，不是本页条数：队列被 limit 截断时前端要能看出来
            "total": store.count_results(
                route=route, pending=pending, question_code=question_code
            ),
            "limit": limit,
            "offset": offset,
            "items": rows,
        }

    @app.post("/api/results/batch-accept", dependencies=[Depends(require_admin)])
    def batch_accept(
        payload: BatchAcceptRequest, store: Store = Depends(require_store)
    ) -> dict:
        reviews = store.accept_high_confidence(
            payload.aggregate_ids, comment=payload.comment
        )
        return {"reviews": [_review_payload(record) for record in reviews]}

    @app.get("/api/results/{aggregate_id}", dependencies=[Depends(require_admin)])
    def get_result(aggregate_id: int, store: Store = Depends(require_store)) -> dict:
        loaded = store.load_evaluation(aggregate_id=aggregate_id)
        if loaded is None:
            # 与 POST 复核走同一个领域异常，保证前端的 404 形状一致（detail + code）
            raise ResultNotFound("评分结果不存在")
        return _detail_payload(
            loaded,
            store.load_review(aggregate_id),
            latest_aggregate_id=store.latest_aggregate_id(loaded.answer_id),
        )

    @app.post("/api/results/{aggregate_id}/review", dependencies=[Depends(require_admin)])
    def review(
        aggregate_id: int,
        payload: ReviewRequest,
        store: Store = Depends(require_store),
    ) -> dict:
        record = store.record_review(
            aggregate_id,
            decision=payload.decision,
            final_score=payload.final_score,
            comment=payload.comment,
        )
        return _review_payload(record)
