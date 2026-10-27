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

from sqlalchemy import Engine, create_engine, event, func, select
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
    grading_tasks,
    item_results,
    questions,
    rubrics,
    task_items,
)


# `item_results.kind` 的取值。写与读都从这里取，避免两边字面量各写一遍。
KIND_ITEM = "item"
KIND_DEDUCTION = "deduction"

# 未提供学号时的脱敏占位符。表里还有一个 SQL 默认值，两处必须一致。
DEFAULT_PSEUDO_ID = "anon"

# task_items.error 与 grading_tasks.error 都是 String(255)，留一点余量
MAX_ERROR_CHARS = 250


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
class TaskRecord:
    task_id: int
    question_code: str
    rubric_id: int
    samples: int
    status: str
    total: int
    done: int
    failed: int
    error: str | None = None


@dataclass(frozen=True)
class TaskItem:
    item_id: int
    task_id: int
    answer_id: int
    status: str
    error: str | None
    aggregate_id: int | None


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


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite 默认**不**检查外键；MySQL 检查。

    不打开它，测试就会放过"插入一条指向不存在答卷的 task_item"这种数据损坏，
    等到换 MySQL 部署才炸。让测试库尽量贴近生产库的行为。
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, connection_record):  # noqa: ANN001
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


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
        if url.startswith("sqlite"):
            # 内存库必须共享同一个连接，否则每次 connect 都是一个空的库。
            # 文件库则相反：每个线程要自己的连接（批量任务是多线程写的），
            # 并用 timeout 容忍短暂的写锁竞争。
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
                kwargs["connect_args"] = {"check_same_thread": False}
            else:
                kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
        engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            _enable_sqlite_foreign_keys(engine)
        return cls(engine)

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

            run_ids, aggregate_id = self._save_runs_and_aggregate(
                conn,
                answer_id=answer_id,
                rubric_id=rubric_id,
                evaluation=evaluation,
                meta=meta,
            )

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

    def save_evaluation_for_answer(
        self,
        *,
        answer_id: int,
        rubric: Rubric,
        evaluation: Evaluation,
        meta: RunMeta | None = None,
        rubric_id: int | None = None,
    ) -> StoredEvaluation:
        """给**已存在**的答卷打分并落库（批量任务走这条路）。

        与 `save_evaluation` 的区别有两点：不再插入 `answers` 行（答卷是导入进来的）；
        可以用 `rubric_id` 指定**任务级快照**，而不是按内容去找/建版本——
        否则标准改过以后，这里会拿旧内容再插入一个新版本，导致
        `aggregate.rubric_id` 与 `task.rubric_id` 不是同一行。
        """
        meta = meta or RunMeta()
        with self._engine.begin() as conn:
            row = conn.execute(
                select(answers.c.question_id, answers.c.raw_text, answers.c.student_pseudo_id)
                .where(answers.c.id == answer_id)
            ).first()
            if row is None:
                raise QuestionNotFound(f"答卷不存在：{answer_id}")
            question_id = row[0]
            if rubric_id is None:
                rubric_id = self._ensure_rubric(conn, question_id, rubric)
            run_ids, aggregate_id = self._save_runs_and_aggregate(
                conn,
                answer_id=answer_id,
                rubric_id=rubric_id,
                evaluation=evaluation,
                meta=meta,
            )

        return StoredEvaluation(
            answer_id=answer_id,
            rubric_id=rubric_id,
            aggregate_id=aggregate_id,
            run_ids=run_ids,
            rubric=rubric,
            answer_text=row[1],
            student_pseudo_id=row[2],
            truncated=False,
            normalized_text=normalize_with_map(row[1])[0],
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

    def _save_runs_and_aggregate(
        self,
        conn,
        *,
        answer_id: int,
        rubric_id: int,
        evaluation: Evaluation,
        meta: "RunMeta",
    ) -> tuple[list[int], int]:
        """写 N 次运行 + 逐条判定 + 聚合结果。批次评分与单份评分共用这一段。"""
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
        return run_ids, aggregate_id

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

    def _recount(self, conn, task_id: int, **extra: Any) -> None:
        """按 task_items 的真实状态重算 done/failed（重置、中断之后必须调）。"""
        counts = conn.execute(
            select(task_items.c.status, func.count())
            .where(task_items.c.task_id == task_id)
            .group_by(task_items.c.status)
        ).all()
        by_status = {status: count for status, count in counts}
        values: dict[str, Any] = {
            "done": by_status.get("graded", 0),
            "failed": by_status.get("failed", 0),
            **extra,
        }
        conn.execute(
            grading_tasks.update().where(grading_tasks.c.id == task_id).values(**values)
        )

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

    # ------------------------------------------------------------------ 批量任务

    def get_answer(self, answer_id: int) -> dict[str, Any] | None:
        """取一份答卷的原文与题号（评分任务要用）。"""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(
                    answers.c.id,
                    answers.c.raw_text,
                    answers.c.student_pseudo_id,
                    questions.c.code,
                )
                .select_from(answers.join(questions, answers.c.question_id == questions.c.id))
                .where(answers.c.id == answer_id)
            ).first()
        if row is None:
            return None
        return {
            "answer_id": row[0],
            "raw_text": row[1],
            "pseudo_id": row[2],
            "question_code": row[3],
        }

    def load_rubric(self, rubric_id: int) -> Rubric | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(rubrics.c.schema_json).where(rubrics.c.id == rubric_id)
            ).first()
        if row is None:
            return None
        return from_dict(row[0])

    def create_task(
        self,
        *,
        question_code: str,
        answer_ids: list[int],
        samples: int,
    ) -> TaskRecord:
        """建任务并**固定当前最新标准**。之后教师再改标准，本批次不受影响。"""
        # 去重但保持顺序：前端勾选重复时会传两次同一个 id，
        # 不去重的话 total 会偏大，插入时还会撞 (task_id, answer_id) 唯一约束
        answer_ids = list(dict.fromkeys(answer_ids))
        if not answer_ids:
            raise ValueError("至少要选一份答卷")
        with self._engine.begin() as conn:
            question = self._question_row(conn, question_code)
            if question is None:
                raise QuestionNotFound(f"题号不存在：{question_code}")
            latest = conn.execute(
                select(rubrics.c.id)
                .where(rubrics.c.question_id == question["id"])
                .order_by(rubrics.c.version.desc())
                .limit(1)
            ).first()
            # 题目只会在保存标准时创建，所以"题目在、标准没有"不该出现；
            # 真出现说明库被绕过写入流程改过，直接报错比猜更安全。
            if latest is None:
                raise QuestionNotFound(f"题号 {question_code} 没有任何评分标准版本")
            rubric_id = latest[0]

            # 校验答卷确实属于这道题：否则会插进一条指向不存在答卷的 task_item，
            # SQLite 不查外键时它还会静默成功，等到列表里"少一份"才发现。
            found = conn.execute(
                select(answers.c.id).where(
                    answers.c.id.in_(answer_ids),
                    answers.c.question_id == question["id"],
                )
            ).scalars().all()
            missing = sorted(set(answer_ids) - set(found))
            if missing:
                raise ValueError(
                    "这些答卷不存在或不属于题号 "
                    f"{question_code}：{'、'.join(str(i) for i in missing)}"
                )

            task_id = conn.execute(
                grading_tasks.insert().values(
                    question_id=question["id"],
                    rubric_id=rubric_id,
                    samples=samples,
                    status="pending",
                    total=len(answer_ids),
                    done=0,
                    failed=0,
                )
            ).inserted_primary_key[0]
            conn.execute(
                task_items.insert(),
                [
                    {"task_id": task_id, "answer_id": answer_id, "status": "pending"}
                    for answer_id in answer_ids
                ],
            )
        return TaskRecord(
            task_id=task_id,
            question_code=question_code,
            rubric_id=rubric_id,
            samples=samples,
            status="pending",
            total=len(answer_ids),
            done=0,
            failed=0,
        )

    def get_task(self, task_id: int) -> TaskRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(
                    grading_tasks.c.id,
                    grading_tasks.c.rubric_id,
                    grading_tasks.c.samples,
                    grading_tasks.c.status,
                    grading_tasks.c.total,
                    grading_tasks.c.done,
                    grading_tasks.c.failed,
                    grading_tasks.c.error,
                    questions.c.code,
                )
                .select_from(
                    grading_tasks.join(questions, grading_tasks.c.question_id == questions.c.id)
                )
                .where(grading_tasks.c.id == task_id)
            ).first()
        if row is None:
            return None
        return TaskRecord(
            task_id=row[0],
            question_code=row[8],
            rubric_id=row[1],
            samples=row[2],
            status=row[3],
            total=row[4],
            done=row[5],
            failed=row[6],
            error=row[7],
        )

    def list_task_items(self, task_id: int) -> list[TaskItem]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    task_items.c.id,
                    task_items.c.task_id,
                    task_items.c.answer_id,
                    task_items.c.status,
                    task_items.c.error,
                    task_items.c.aggregate_id,
                )
                .where(task_items.c.task_id == task_id)
                .order_by(task_items.c.id)
            ).all()
        return [
            TaskItem(
                item_id=row[0],
                task_id=row[1],
                answer_id=row[2],
                status=row[3],
                error=row[4],
                aggregate_id=row[5],
            )
            for row in rows
        ]

    def claim_next_item(self, task_id: int) -> TaskItem | None:
        """把下一个待评的答卷标记为 running 并返回。

        用"先读候选、再带条件更新"的 CAS 循环，而不是先 UPDATE 再看结果：
        两个 worker 可能同时读到同一行 pending，带 `status='pending'` 条件的 UPDATE
        只有第一个能改成功（rowcount=1），第二个会重试到下一条。
        单进程下也能用锁解决，但 CAS 在 MySQL 多进程部署时同样成立。
        """
        # 正常一两轮就抢到或返回 None；上限只为防御"读到却被删"这种怪状态，别真的空转
        for _ in range(1000):
            with self._engine.begin() as conn:
                row = conn.execute(
                    select(task_items.c.id, task_items.c.answer_id)
                    .where(task_items.c.task_id == task_id, task_items.c.status == "pending")
                    .order_by(task_items.c.id)
                    .limit(1)
                ).first()
                if row is None:
                    return None
                claimed = conn.execute(
                    task_items.update()
                    .where(task_items.c.id == row[0], task_items.c.status == "pending")
                    .values(status="running", updated_at=func.now())
                ).rowcount
                if claimed == 1:
                    return TaskItem(
                        item_id=row[0],
                        task_id=task_id,
                        answer_id=row[1],
                        status="running",
                        error=None,
                        aggregate_id=None,
                    )
            # 被别人抢走了，回到循环再取一条
        return None

    def finish_task_item(
        self,
        item_id: int,
        *,
        status: str,
        aggregate_id: int | None = None,
        error: str | None = None,
    ) -> None:
        """记录一份答卷的结局，并用**增量**方式维护任务的完成/失败计数。

        不用"查出来再写回去"：MySQL 默认隔离级别下，两个 worker 同时完成会互相覆盖。
        `done = done + 1` 是原子操作，谁先谁后都不会丢。
        """
        with self._engine.begin() as conn:
            row = conn.execute(
                select(task_items.c.task_id, task_items.c.status).where(
                    task_items.c.id == item_id
                )
            ).first()
            if row is None:
                return
            task_id, previous = row
            conn.execute(
                task_items.update()
                .where(task_items.c.id == item_id)
                .values(
                    status=status,
                    aggregate_id=aggregate_id,
                    error=(error[:MAX_ERROR_CHARS] if error else None),
                    updated_at=func.now(),
                )
            )
            # 只有"从别的状态变成 graded/failed"才加一，重复落同一条不会把计数刷爆
            if status == "graded" and previous != "graded":
                conn.execute(
                    grading_tasks.update()
                    .where(grading_tasks.c.id == task_id)
                    .values(done=grading_tasks.c.done + 1)
                )
            elif status == "failed" and previous != "failed":
                conn.execute(
                    grading_tasks.update()
                    .where(grading_tasks.c.id == task_id)
                    .values(failed=grading_tasks.c.failed + 1)
                )

    def task_item_views(self, task_id: int) -> list[dict[str, Any]]:
        """任务里每份答卷的状态 + 分数 + 路由。

        "待复核"不单独存状态：它就是 `aggregated_results.route == 'human'`，
        存两份迟早会出现"状态说已评、路由说待复核"的自相矛盾。
        """
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    task_items.c.answer_id,
                    answers.c.student_pseudo_id,
                    task_items.c.status,
                    task_items.c.error,
                    task_items.c.aggregate_id,
                    aggregated_results.c.raw_score,
                    aggregated_results.c.max_score,
                    aggregated_results.c.route,
                    aggregated_results.c.confidence,
                )
                .select_from(
                    task_items.join(answers, task_items.c.answer_id == answers.c.id).outerjoin(
                        aggregated_results,
                        task_items.c.aggregate_id == aggregated_results.c.id,
                    )
                )
                .where(task_items.c.task_id == task_id)
                .order_by(task_items.c.id)
            ).all()
        return [
            {
                "answer_id": row[0],
                "pseudo_id": row[1],
                "status": row[2],
                "error": row[3],
                "aggregate_id": row[4],
                "score": row[5],
                "max_score": row[6],
                "route": row[7],
                "confidence": row[8],
            }
            for row in rows
        ]

    def set_task_status(self, task_id: int, status: str, *, error: str | None = None) -> None:
        values: dict[str, Any] = {"status": status}
        if error is not None:
            values["error"] = error[:MAX_ERROR_CHARS]
        if status == "running":
            values["started_at"] = func.now()
        if status in {"done", "interrupted"}:
            values["finished_at"] = func.now()
        with self._engine.begin() as conn:
            conn.execute(
                grading_tasks.update().where(grading_tasks.c.id == task_id).values(**values)
            )

    def reset_failed_items(self, task_id: int, *, answer_ids: list[int] | None = None) -> int:
        """把失败项放回 pending，供重试。返回重置了几条。

        `answer_ids` 为空表示"这一批所有失败项"；给了就只重置这几份
        （验收要的"失败项可单独重试"）。
        """
        with self._engine.begin() as conn:
            conditions = [
                task_items.c.task_id == task_id,
                task_items.c.status == "failed",
            ]
            if answer_ids:
                conditions.append(task_items.c.answer_id.in_(answer_ids))
            rows = conn.execute(select(task_items.c.id).where(*conditions)).scalars().all()
            if not rows:
                return 0
            conn.execute(
                task_items.update()
                .where(task_items.c.id.in_(rows))
                .values(status="pending", error=None, updated_at=func.now())
            )
            # 重算而不是清零：只重试子集时，其余失败项还在
            self._recount(conn, task_id, status="pending", finished_at=None)
        return len(rows)

    def mark_interrupted_tasks(self) -> int:
        """进程重启后，把停在 running 的任务标为 interrupted。

        不做这一步，任务会永远显示"进行中"，教师以为它还在跑。
        """
        with self._engine.begin() as conn:
            running = conn.execute(
                select(grading_tasks.c.id).where(grading_tasks.c.status == "running")
            ).scalars().all()
            if not running:
                return 0
            conn.execute(
                grading_tasks.update()
                .where(grading_tasks.c.id.in_(running))
                .values(
                    status="interrupted",
                    error="进程重启，任务中断（可重试）",
                    finished_at=func.now(),
                )
            )
            conn.execute(
                task_items.update()
                .where(
                    task_items.c.task_id.in_(running), task_items.c.status == "running"
                )
                .values(status="failed", error="进程重启，任务中断", updated_at=func.now())
            )
            for task_id in running:
                # 不重算的话，任务会显示 failed=0 而每一份其实都失败了
                self._recount(conn, task_id)
        return len(running)

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
