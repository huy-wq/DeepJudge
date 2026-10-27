"""存储层：把一次评分完整落库，并按答卷号或运行号读回。

设计取舍：
- **写入是一个事务**。标准快照、答卷、N 次运行、逐条判定、聚合结果要么全进，要么全不进；
  半份数据比没有数据更危险——复核的人会以为那就是当时的结论。
- **标准只增不改**。`rubrics` 的每一行都是一份不可变快照，运行用外键指向它，
  所以标准改了以后旧运行仍能原样显示当时的标准（架构 §4.4）。
- **读回的对象就是领域对象**。`load_evaluation` 直接还原 `Evaluation`，
  不另造一套"DTO 字段名差不多"的结构；字段对不上就说明真的丢了东西。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.pool import StaticPool

from ..core.aggregate import Aggregate
from ..core.normalize import hash_normalized, normalize_with_map
from ..core.routing import RouteDecision
from ..core.rubric import from_dict, to_dict
from ..core.types import DeductionJudgement, ItemJudgement, Rubric, SampleJudgement
from ..pipeline import Evaluation
from .tables import (
    aggregated_results,
    answers,
    grading_runs,
    item_results,
    questions,
    rubrics,
)


# `item_results.kind` 的取值。写与读都从这里取，避免两边字面量各写一遍。
KIND_ITEM = "item"
KIND_DEDUCTION = "deduction"

# 未提供学号时的脱敏占位符。表里还有一个 SQL 默认值，两处必须一致。
DEFAULT_PSEUDO_ID = "anon"


@dataclass(frozen=True)
class RunMeta:
    """一次运行的外部信息：谁调的、什么模型、花了多少。"""

    model: str = ""
    prompt_version: str = "v1"
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class RunRecord:
    """一次运行的外部信息，读回时与判定结果一起返回。

    领域对象 `SampleJudgement` 里没有这些字段（评分逻辑用不到），但**审计需要**：
    同样一份 0.5 分，是哪个模型、什么参数、花了多少、有没有报错，都要能查。
    """

    run_id: int
    rubric_id: int
    model: str
    prompt_version: str
    params: dict[str, Any]
    status: str
    error: str | None
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int | None


class QuestionNotFound(LookupError):
    """要导入的题号在库里不存在——通常是因为还没保存过这份评分标准。"""


@dataclass(frozen=True)
class ImportedAnswer:
    answer_id: int
    question_code: str
    pseudo_id: str
    content_hash: str


@dataclass(frozen=True)
class ImportOutcome:
    imported: list[ImportedAnswer]
    skipped_duplicates: int


@dataclass(frozen=True)
class StoredEvaluation:
    """保存结果，也是读回的返回类型。

    分三层装同一次评分：答卷（谁写的）、运行（怎么评的）、结果（评成什么）。
    任何一层缺失，"按答卷或运行编号读回完整结果"就不成立。
    """

    answer_id: int
    rubric_id: int
    aggregate_id: int
    run_ids: list[int]
    rubric: Rubric
    answer_text: str
    student_pseudo_id: str
    truncated: bool
    normalized_text: str
    runs: list[RunRecord]
    evaluation: Evaluation


def _checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _span_of(row) -> tuple[int, int] | None:
    if row["span_start"] is None or row["span_end"] is None:
        return None
    return (row["span_start"], row["span_end"])


def _level_of(row) -> float:
    # 采分点的 level 在表里 NOT NULL（只有扣分项允许 NULL）。
    # 这里若真的是 NULL，说明数据被绕过写入流程改坏了——宁可炸，也不要凭空补一个 0 分。
    if row["level"] is None:
        raise ValueError(
            f"运行 {row['run_id']} 的采分点 {row['rubric_item_id']} 没有等级，数据不符合表约束"
        )
    return float(row["level"])


def _score_of(run) -> float:
    # 失败的运行没有分数。把它读成 0 分，等于把"没评成"记成"评了 0 分"。
    if run["score"] is None:
        raise ValueError(
            f"运行 {run['id']} 没有分数（状态 {run['status']}），失败的运行不应出现在聚合里"
        )
    return float(run["score"])


def _span_columns(span: tuple[int, int] | None) -> dict[str, int | None]:
    return {"span_start": span[0], "span_end": span[1]} if span else {
        "span_start": None,
        "span_end": None,
    }


class Store:
    """存储入口。一个实例持有一个 Engine，线程安全由 Engine 的连接池保证。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def from_url(cls, url: str, *, echo: bool = False) -> "Store":
        kwargs: dict[str, Any] = {"echo": echo}
        # 内存库必须共享同一个连接，否则每次 connect 都是一个空的库。
        if url.startswith("sqlite") and ":memory:" in url:
            kwargs["poolclass"] = StaticPool
            kwargs["connect_args"] = {"check_same_thread": False}
        return cls(create_engine(url, **kwargs))

    @property
    def engine(self) -> Engine:
        return self._engine

    def dispose(self) -> None:
        self._engine.dispose()

    # ------------------------------------------------------------------ 写入

    def save_evaluation(
        self,
        *,
        rubric: Rubric,
        answer_text: str,
        evaluation: Evaluation,
        meta: RunMeta | None = None,
        student_pseudo_id: str = DEFAULT_PSEUDO_ID,
        truncated: bool = False,
    ) -> StoredEvaluation:
        meta = meta or RunMeta()
        normalized_text = normalize_with_map(answer_text)[0]
        with self._engine.begin() as conn:
            question_id = self._ensure_question(conn, rubric)
            rubric_id = self._ensure_rubric(conn, question_id, rubric)
            answer_id = conn.execute(
                answers.insert().values(
                    question_id=question_id,
                    student_pseudo_id=student_pseudo_id,
                    raw_text=answer_text,
                    normalized_text=normalized_text,
                    content_hash=hash_normalized(normalized_text),
                    source="paste",
                    truncated=truncated,
                )
            ).inserted_primary_key[0]

            run_ids: list[int] = []
            for sample in evaluation.samples:
                run_id = conn.execute(
                    grading_runs.insert().values(
                        answer_id=answer_id,
                        rubric_id=rubric_id,
                        prompt_version=meta.prompt_version,
                        model=meta.model,
                        params_json=dict(meta.params),
                        status=meta.status,
                        score=sample.score,
                        comment=sample.comment,
                        tokens_in=meta.tokens_in,
                        tokens_out=meta.tokens_out,
                        latency_ms=meta.latency_ms,
                        error=meta.error,
                    )
                ).inserted_primary_key[0]
                run_ids.append(run_id)
                self._insert_judgements(conn, run_id, sample)

            combined = evaluation.aggregate
            aggregate_id = conn.execute(
                aggregated_results.insert().values(
                    answer_id=answer_id,
                    rubric_id=rubric_id,
                    sample_run_ids=run_ids,
                    levels_json=combined.levels,
                    sample_totals_json=combined.sample_totals,
                    raw_score=combined.score,
                    calibrated_score=None,
                    max_score=combined.max_score,
                    dispersion=combined.dispersion,
                    total_range=combined.total_range,
                    confidence=combined.confidence,
                    grounding_rate=combined.grounding_rate,
                    route=evaluation.route.route,
                    risk=evaluation.route.risk,
                    reasons_json=evaluation.route.reasons,
                    applied_deductions_json=combined.applied_deductions,
                    calibration_version=None,
                )
            ).inserted_primary_key[0]

        return StoredEvaluation(
            answer_id=answer_id,
            rubric_id=rubric_id,
            aggregate_id=aggregate_id,
            run_ids=run_ids,
            rubric=rubric,
            answer_text=answer_text,
            student_pseudo_id=student_pseudo_id,
            truncated=truncated,
            normalized_text=normalized_text,
            runs=[
                RunRecord(
                    run_id=run_id,
                    rubric_id=rubric_id,
                    model=meta.model,
                    prompt_version=meta.prompt_version,
                    params=dict(meta.params),
                    status=meta.status,
                    error=meta.error,
                    tokens_in=meta.tokens_in,
                    tokens_out=meta.tokens_out,
                    latency_ms=meta.latency_ms,
                )
                for run_id in run_ids
            ],
            evaluation=evaluation,
        )

    def _ensure_question(self, conn, rubric: Rubric) -> int:
        row = conn.execute(
            select(questions.c.id).where(questions.c.code == rubric.question_id)
        ).first()
        if row is not None:
            return row[0]
        return conn.execute(
            questions.insert().values(
                code=rubric.question_id,
                stem=rubric.stem,
                total_score=rubric.total_score,
                status="active",
            )
        ).inserted_primary_key[0]

    def _ensure_rubric(self, conn, question_id: int, rubric: Rubric) -> int:
        """内容没变就复用最新版本，变了就追加一版；已有版本永不修改。"""
        payload = to_dict(rubric)
        digest = _checksum(payload)

        latest = conn.execute(
            select(rubrics.c.id, rubrics.c.version, rubrics.c.checksum)
            .where(rubrics.c.question_id == question_id)
            .order_by(rubrics.c.version.desc())
            .limit(1)
        ).first()
        if latest is not None and latest[2] == digest:
            return latest[0]

        next_version = 1 if latest is None else latest[1] + 1
        return conn.execute(
            rubrics.insert().values(
                question_id=question_id,
                version=next_version,
                schema_json=payload,
                checksum=digest,
            )
        ).inserted_primary_key[0]

    def _insert_judgements(self, conn, run_id: int, sample) -> None:
        rows = [
            {
                "run_id": run_id,
                "kind": KIND_ITEM,
                "rubric_item_id": judgement.item_id,
                "level": judgement.level,
                "hit": judgement.hit,
                "evidence": judgement.evidence,
                "grounded": judgement.grounded,
                "reason": judgement.reason,
                **_span_columns(judgement.span),
            }
            for judgement in sample.items
        ] + [
            {
                "run_id": run_id,
                "kind": KIND_DEDUCTION,
                "rubric_item_id": deduction.id,
                "level": None,
                "hit": deduction.hit,
                "evidence": deduction.evidence,
                "grounded": deduction.grounded,
                "reason": deduction.reason,
                **_span_columns(deduction.span),
            }
            for deduction in sample.deductions
        ]
        if rows:
            conn.execute(item_results.insert(), rows)

    # ------------------------------------------------------------------ 答卷导入

    def get_question(self, code: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            return self._question_row(conn, code)

    def find_duplicates(self, pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
        """给定一批 (题号, 内容指纹)，返回库里已存在的那些。

        **按 (题号, 指纹) 成对判断**：两道题出现同样的文字不算重复——
        把各题的指纹并成一个集合再判断，会让 Q-01 的答案是"三次握手"时，
        Q-02 里同样的"三次握手"被误标成重复。
        """
        if not pairs:
            return set()
        by_code: dict[str, set[str]] = {}
        for code, digest in pairs:
            by_code.setdefault(code, set()).add(digest)

        found: set[tuple[str, str]] = set()
        with self._engine.connect() as conn:
            for code, digests in by_code.items():
                question = self._question_row(conn, code)
                if question is None:
                    continue
                rows = conn.execute(
                    select(answers.c.content_hash).where(
                        answers.c.question_id == question["id"],
                        answers.c.content_hash.in_(sorted(digests)),
                    )
                ).scalars().all()
                found.update((code, digest) for digest in rows)
        return found

    def import_answers(
        self,
        rows: list[tuple[str, str]],
        *,
        source: str,
        skip_duplicates: bool = True,
    ) -> ImportOutcome:
        """把 (题号, 答案) 批量入库。**一个事务**：要么全进，要么全不进。

        学号在解析阶段就丢了，这里只生成与学号无关的脱敏编号（`S-0001`…）。
        """
        if not rows:
            return ImportOutcome(imported=[], skipped_duplicates=0)

        imported: list[ImportedAnswer] = []
        skipped = 0
        with self._engine.begin() as conn:
            questions_by_code: dict[str, dict] = {}
            unknown: list[str] = []
            for code, _ in rows:
                if code in questions_by_code:
                    continue
                question = self._question_row(conn, code)
                if question is None:
                    unknown.append(code)
                else:
                    questions_by_code[code] = question
            if unknown:
                raise QuestionNotFound(
                    "题号不存在：" + "、".join(sorted(set(unknown))) + "（先保存这份评分标准，再导入答卷）"
                )

            next_seq = {
                code: self._next_pseudo_seq(conn, question["id"])
                for code, question in questions_by_code.items()
            }
            seen: set[tuple[int, str]] = set()

            for code, text in rows:
                question = questions_by_code[code]
                normalized = normalize_with_map(text)[0]
                digest = hash_normalized(normalized)
                if skip_duplicates:
                    if (question["id"], digest) in seen or self._hash_exists(
                        conn, question["id"], digest
                    ):
                        skipped += 1
                        continue
                seen.add((question["id"], digest))

                pseudo_id = f"S-{next_seq[code]:04d}"
                next_seq[code] += 1
                answer_id = conn.execute(
                    answers.insert().values(
                        question_id=question["id"],
                        student_pseudo_id=pseudo_id,
                        raw_text=text,
                        normalized_text=normalized,
                        content_hash=digest,
                        source=source,
                    )
                ).inserted_primary_key[0]
                imported.append(
                    ImportedAnswer(
                        answer_id=answer_id,
                        question_code=code,
                        pseudo_id=pseudo_id,
                        content_hash=digest,
                    )
                )

        return ImportOutcome(imported=imported, skipped_duplicates=skipped)

    def list_answers(self, question_code: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """列出某题的答卷（不含原文，列表用不着整段文本）。"""
        with self._engine.connect() as conn:
            question = self._question_row(conn, question_code)
            if question is None:
                return []
            rows = conn.execute(
                select(
                    answers.c.id,
                    answers.c.student_pseudo_id,
                    answers.c.source,
                    answers.c.created_at,
                    func.length(answers.c.raw_text),
                )
                .where(answers.c.question_id == question["id"])
                .order_by(answers.c.id)
                .limit(limit)
            ).all()
        return [
            {
                "answer_id": row[0],
                "pseudo_id": row[1],
                "source": row[2],
                "created_at": row[3],
                "length": row[4],
            }
            for row in rows
        ]

    def count_answers(self, question_code: str) -> int:
        with self._engine.connect() as conn:
            question = self._question_row(conn, question_code)
            if question is None:
                return 0
            return conn.execute(
                select(func.count()).select_from(answers).where(
                    answers.c.question_id == question["id"]
                )
            ).scalar_one()

    def _question_row(self, conn, code: str) -> dict[str, Any] | None:
        row = conn.execute(
            select(questions.c.id, questions.c.code, questions.c.stem, questions.c.total_score)
            .where(questions.c.code == code)
        ).first()
        if row is None:
            return None
        return {
            "id": row[0],
            "code": row[1],
            "stem": row[2],
            "total_score": row[3],
        }

    def _hash_exists(self, conn, question_id: int, digest: str) -> bool:
        return (
            conn.execute(
                select(answers.c.id).where(
                    answers.c.question_id == question_id,
                    answers.c.content_hash == digest,
                )
            ).first()
            is not None
        )

    def _next_pseudo_seq(self, conn, question_id: int) -> int:
        """下一个脱敏编号的序号：取该题已有编号的最大值 +1，与学号无关。"""
        used = conn.execute(
            select(answers.c.student_pseudo_id).where(answers.c.question_id == question_id)
        ).scalars().all()
        numbers = [
            int(match.group(1))
            for pid in used
            if (match := re.fullmatch(r"S-(\d+)", pid or ""))
        ]
        return max(numbers) + 1 if numbers else 1

    # ------------------------------------------------------------------ 读取

    def load_evaluation(
        self, *, answer_id: int | None = None, run_id: int | None = None
    ) -> StoredEvaluation | None:
        """按答卷号或运行号读回完整结果。

        - 按答卷号：取该答卷**最新**的聚合结果（重评一次就多一行，最新即当前结论）。
        - 按运行号：取**包含这一次运行**的那次聚合。不能图省事取最新——重评之后，
          这条运行和最新结论已经不是一回事了，硬拿最新的会把两次评分混成一次。
        """
        if (answer_id is None) == (run_id is None):
            raise ValueError("answer_id 与 run_id 必须且只能给一个")

        with self._engine.connect() as conn:
            if answer_id is None:
                run = conn.execute(
                    select(grading_runs.c.answer_id).where(grading_runs.c.id == run_id)
                ).first()
                if run is None:
                    return None
                answer_id = run[0]

            answer = conn.execute(
                select(answers).where(answers.c.id == answer_id)
            ).mappings().first()
            if answer is None:
                return None

            aggregate_row = self._find_aggregate(conn, answer_id, run_id)
            if aggregate_row is None:
                return None

            rubric_row = conn.execute(
                select(rubrics).where(rubrics.c.id == aggregate_row["rubric_id"])
            ).mappings().first()
            if rubric_row is None:
                return None
            rubric = from_dict(rubric_row["schema_json"])

            run_ids = list(aggregate_row["sample_run_ids"])
            loaded = [(rid, self._load_sample(conn, rid, rubric)) for rid in run_ids]
            if any(sample is None for sample in loaded):
                return None
            samples = [sample for _, sample in loaded]
            runs = [self._load_run_meta(conn, rid) for rid in run_ids]
            if any(record is None for record in runs):
                return None

            combined = Aggregate(
                levels=dict(aggregate_row["levels_json"]),
                score=aggregate_row["raw_score"],
                max_score=aggregate_row["max_score"],
                dispersion=aggregate_row["dispersion"],
                confidence=aggregate_row["confidence"],
                sample_totals=list(aggregate_row["sample_totals_json"]),
                total_range=aggregate_row["total_range"],
                grounding_rate=aggregate_row["grounding_rate"],
                applied_deductions=list(aggregate_row["applied_deductions_json"]),
            )
            route = RouteDecision(
                route=aggregate_row["route"],
                risk=aggregate_row["risk"],
                reasons=list(aggregate_row["reasons_json"]),
            )

        return StoredEvaluation(
            answer_id=answer_id,
            rubric_id=rubric_row["id"],
            aggregate_id=aggregate_row["id"],
            run_ids=run_ids,
            rubric=rubric,
            answer_text=answer["raw_text"],
            student_pseudo_id=answer["student_pseudo_id"],
            truncated=bool(answer["truncated"]),
            normalized_text=answer["normalized_text"],
            runs=runs,
            evaluation=Evaluation(samples=samples, aggregate=combined, route=route),
        )

    def _find_aggregate(self, conn, answer_id: int, run_id: int | None):
        """按答卷号取最新一行；按运行号取第一行"真的包含这条运行"的聚合。"""
        rows = conn.execute(
            select(aggregated_results)
            .where(aggregated_results.c.answer_id == answer_id)
            .order_by(aggregated_results.c.id.desc())
        ).mappings().all()
        if run_id is None:
            return rows[0] if rows else None
        for row in rows:
            if run_id in list(row["sample_run_ids"]):
                return row
        return None

    def _load_run_meta(self, conn, run_id: int) -> RunRecord | None:
        row = conn.execute(
            select(grading_runs).where(grading_runs.c.id == run_id)
        ).mappings().first()
        if row is None:
            return None
        return RunRecord(
            run_id=row["id"],
            rubric_id=row["rubric_id"],
            model=row["model"],
            prompt_version=row["prompt_version"],
            params=dict(row["params_json"]),
            status=row["status"],
            error=row["error"],
            tokens_in=row["tokens_in"],
            tokens_out=row["tokens_out"],
            latency_ms=row["latency_ms"],
        )

    def _load_sample(self, conn, run_id: int, rubric: Rubric):
        """还原一次采样。rubric 由调用方给：同一次评分的所有运行共用一份标准。"""
        run = conn.execute(
            select(grading_runs).where(grading_runs.c.id == run_id)
        ).mappings().first()
        if run is None:
            return None

        rows = conn.execute(
            select(item_results)
            .where(item_results.c.run_id == run_id)
            .order_by(item_results.c.id)
        ).mappings().all()

        items = [
            ItemJudgement(
                item_id=row["rubric_item_id"],
                level=_level_of(row),
                hit=bool(row["hit"]),
                evidence=row["evidence"],
                span=_span_of(row),
                grounded=bool(row["grounded"]),
                reason=row["reason"],
            )
            for row in rows
            if row["kind"] == KIND_ITEM
        ]
        deductions = [
            DeductionJudgement(
                id=row["rubric_item_id"],
                hit=bool(row["hit"]),
                evidence=row["evidence"],
                span=_span_of(row),
                grounded=bool(row["grounded"]),
                reason=row["reason"],
            )
            for row in rows
            if row["kind"] == KIND_DEDUCTION
        ]
        return SampleJudgement(
            question_id=rubric.question_id,
            items=items,
            deductions=deductions,
            comment=run["comment"],
            score=_score_of(run),
            max_score=rubric.total_score,
        )
