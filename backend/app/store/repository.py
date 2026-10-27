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
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from ..core.aggregate import Aggregate
from ..core.normalize import hash_normalized, normalize_with_map
from ..core.routing import RouteDecision
from ..core.rubric import from_dict, to_dict
from ..core.types import EPS, DeductionJudgement, ItemJudgement, Rubric, SampleJudgement
from ..pipeline import Evaluation
from .tables import (
    aggregated_results,
    answers,
    grading_runs,
    grading_tasks,
    item_results,
    questions,
    reviews,
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


class ResultNotFound(LookupError):
    """要复核的评分结果不存在。"""


class RubricNotFound(LookupError):
    """要打开或迭代的评分标准版本不存在。"""


class ResultFinalized(Exception):
    """这份结果已经有复核记录，即已定稿。

    定稿是**一次**的：错误地改分或重复点击都不该悄悄覆盖一个已经成立的结论。
    需要更正时走后续的撤销/重开流程（当前版本没有）。
    """


class ResultSuperseded(Exception):
    """这份结果已被同一份答卷的重新评分取代，不该再定稿。

    队列里已经把它藏起来；接口再挡一次，防止有人拿着旧页面的链接
    把一个过期的结论"确认"成正式成绩。
    """


class ReviewRejected(ValueError):
    """复核请求本身不成立：改分没有意见、分数越界、批量通过里混了待复核项。"""


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
class ReviewRecord:
    """一次复核：谁、把最终分定成多少、采纳还是改分、为什么。"""

    review_id: int
    aggregate_id: int
    reviewer_id: str
    final_score: float
    decision: str
    comment: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class SavedRubric:
    """一份被保存（或命中最新的同内容版本）的评分标准。"""

    rubric_id: int
    question_pk: int
    question_code: str
    version: int
    checksum: str
    parent_rubric_id: int | None
    created_at: datetime | None
    reused: bool


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


REVIEW_DECISIONS = {"accept", "override"}


def _reviewed_score(
    *, decision: str, given: float | None, raw: float, max_score: float
) -> float:
    """复核后的最终分：采纳时就是原分，改分时校验取值范围。"""
    value: float | None = None
    if given is not None:
        try:
            value = float(given)
        except (TypeError, ValueError) as exc:
            raise ReviewRejected("最终分必须是数字") from exc

    # NaN 与 `value < 0`、`value > max_score` 两个比较都是 False，不挡住的话会一路写进库
    # （SQLite 会把它存成 NULL），最后撞 NOT NULL 约束，报出来的却是"已定稿"——错误信息与事实完全不符。
    if value is not None and not math.isfinite(value):
        raise ReviewRejected("最终分必须是有限数值")

    if decision == "accept":
        if value is not None and abs(value - raw) > EPS:
            raise ReviewRejected(
                "采纳就是接受模型给的分；要改分请选择改分并写明理由"
            )
        return round(raw, 2)

    if value is None:
        raise ReviewRejected("改分必须给出最终分")
    if value < 0 or value > max_score:
        raise ReviewRejected(f"最终分必须在 0 到 {max_score:g} 之间")
    return round(value, 2)


def _review_record(row) -> ReviewRecord:
    return ReviewRecord(
        review_id=row["id"],
        aggregate_id=row["aggregated_result_id"],
        reviewer_id=row["reviewer_id"],
        final_score=row["final_score"],
        decision=row["decision"],
        comment=row["comment"],
        created_at=row["created_at"],
    )


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
            # 先锁住该答卷当前最新的聚合结果，再判"有没有定稿"。
            # 复核走的是同一条记录上的 FOR UPDATE，两个方向因此串行化：
            # 没有它，"复核先查后插"与"重评先查后插"可以同时通过检查（MySQL 下真实存在）。
            # SQLite 会忽略 FOR UPDATE，但同一时刻只有一个写者，行为仍然一致。
            conn.execute(
                select(aggregated_results.c.id)
                .where(aggregated_results.c.answer_id == answer_id)
                .order_by(aggregated_results.c.id.desc())
                .limit(1)
                .with_for_update()
            ).first()
            if self._finalized_answer_ids(conn, [answer_id]):
                raise ResultFinalized("这份答卷已经定稿，不能重新评分")
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

    def _question_id_of(self, conn, code: str, *, lock: bool = False) -> int | None:
        query = select(questions.c.id).where(questions.c.code == code)
        if lock:
            query = query.with_for_update()
        row = conn.execute(query).first()
        return None if row is None else row[0]

    def _ensure_question(self, conn, rubric: Rubric) -> int:
        """评分写入路径专用：题目只在第一次出现时创建，不改题干与满分。"""
        question_id = self._question_id_of(conn, rubric.question_id)
        if question_id is not None:
            return question_id
        return conn.execute(
            questions.insert().values(
                code=rubric.question_id,
                stem=rubric.stem,
                total_score=rubric.total_score,
                status="active",
            )
        ).inserted_primary_key[0]

    def _upsert_question(self, conn, rubric: Rubric) -> int:
        """保存标准路径专用：题干与满分跟着这一版走。

        标准里存着 stem / total_score，`questions` 表里也存一份（列表页要读）。
        只创建不更新，就会留下"标准说 12 分、题目说 10 分"这种两套真相。

        先锁题目行：两个并发的"保存"会各算一次 version+1，第二个撞
        `uq_rubrics_question_version`。加锁后它们串行化（MySQL）；题目还不存在时，
        InnoDB 的间隙锁同样挡住"同时插同一题号"。
        """
        question_id = self._question_id_of(conn, rubric.question_id, lock=True)
        if question_id is None:
            return conn.execute(
                questions.insert().values(
                    code=rubric.question_id,
                    stem=rubric.stem,
                    total_score=rubric.total_score,
                    status="active",
                )
            ).inserted_primary_key[0]
        conn.execute(
            questions.update()
            .where(questions.c.id == question_id)
            .values(stem=rubric.stem, total_score=rubric.total_score)
        )
        return question_id

    def _append_rubric(
        self,
        conn,
        question_id: int,
        rubric: Rubric,
        *,
        source_text: str = "",
        feedback: str = "",
        parent_rubric_id: int | None = None,
    ) -> tuple[int, int, str, bool]:
        """内容没变就复用最新版本，变了就追加一版；已有版本永不修改。

        返回 (rubric_id, version, checksum, reused)。
        """
        payload = to_dict(rubric)
        digest = _checksum(payload)

        latest = conn.execute(
            select(rubrics.c.id, rubrics.c.version, rubrics.c.checksum)
            .where(rubrics.c.question_id == question_id)
            .order_by(rubrics.c.version.desc())
            .limit(1)
        ).first()
        if latest is not None and latest[2] == digest:
            return latest[0], latest[1], latest[2], True

        next_version = 1 if latest is None else latest[1] + 1
        try:
            rubric_id = conn.execute(
                rubrics.insert().values(
                    question_id=question_id,
                    version=next_version,
                    schema_json=payload,
                    checksum=digest,
                    source_text=source_text,
                    feedback=feedback,
                    parent_rubric_id=parent_rubric_id,
                )
            ).inserted_primary_key[0]
        except IntegrityError:
            # 两个并发保存各算了一次 version+1：后到的撞唯一约束。
            # 如果对方写的正是同一份内容，就复用它（这才是常见情况：同一份标准被存两次）。
            latest = conn.execute(
                select(rubrics.c.id, rubrics.c.version, rubrics.c.checksum)
                .where(rubrics.c.question_id == question_id)
                .order_by(rubrics.c.version.desc())
                .limit(1)
            ).first()
            if latest is not None and latest[2] == digest:
                return latest[0], latest[1], latest[2], True
            raise
        return rubric_id, next_version, digest, False

    def _ensure_rubric(self, conn, question_id: int, rubric: Rubric) -> int:
        return self._append_rubric(conn, question_id, rubric)[0]

    # ------------------------------------------------------------------ 标准的保存与迭代

    def save_rubric(
        self,
        rubric: Rubric,
        *,
        source_text: str = "",
        feedback: str = "",
        based_on_rubric_id: int | None = None,
    ) -> SavedRubric:
        """保存一份标准（新建或改完再存）。

        **版本只增不改**：内容与最新版相同就复用那一行，不同就追加一版。
        这样"教师确认过的某一版"永远能被任务快照指回去（架构 §5.5）。

        `based_on_rubric_id` 记录"这一版是从哪一版改出来的"——版本号只说明先后，
        不说明来源；改了旧版本再存时，两者会不一致。
        """
        with self._engine.begin() as conn:
            base_question = None
            if based_on_rubric_id is not None:
                base = conn.execute(
                    select(rubrics.c.id, rubrics.c.question_id).where(
                        rubrics.c.id == based_on_rubric_id
                    )
                ).first()
                if base is None:
                    raise RubricNotFound(f"评分标准版本不存在：{based_on_rubric_id}")
                base_question = base[1]
            question_id = self._upsert_question(conn, rubric)
            if base_question is not None and base_question != question_id:
                raise ValueError("基线标准不属于同一道题，不能在这上面继续改")
            rubric_id, version, digest, reused = self._append_rubric(
                conn,
                question_id,
                rubric,
                source_text=source_text,
                feedback=feedback,
                parent_rubric_id=based_on_rubric_id,
            )
            if reused:
                # 评分写入路径可能先建过同一内容、但没带原话；这次保存把空着的溯源补上。
                # 只补空值，不覆盖已有的原话/意见。
                self._fill_provenance(conn, rubric_id, source_text, feedback)
            row = conn.execute(
                select(rubrics.c.created_at, rubrics.c.parent_rubric_id).where(
                    rubrics.c.id == rubric_id
                )
            ).first()
        return SavedRubric(
            rubric_id=rubric_id,
            question_pk=question_id,
            question_code=rubric.question_id,
            version=version,
            checksum=digest,
            parent_rubric_id=row[1],
            created_at=row[0],
            reused=reused,
        )

    def _fill_provenance(
        self, conn, rubric_id: int, source_text: str, feedback: str
    ) -> None:
        values: dict[str, Any] = {}
        if source_text.strip():
            values["source_text"] = source_text
        if feedback.strip():
            values["feedback"] = feedback
        if not values:
            return
        row = conn.execute(
            select(rubrics.c.source_text, rubrics.c.feedback).where(
                rubrics.c.id == rubric_id
            )
        ).first()
        if row is None:
            return
        updates = {
            key: value
            for key, value in values.items()
            if not (row[0] if key == "source_text" else row[1]).strip()
        }
        if updates:
            conn.execute(
                rubrics.update().where(rubrics.c.id == rubric_id).values(**updates)
            )

    def list_rubrics(self) -> list[dict[str, Any]]:
        """列出每道题**最新一版**标准（列表页只需要最新版；历史版本按 id 打开）。"""
        latest_version = (
            select(func.max(rubrics.c.version))
            .where(rubrics.c.question_id == questions.c.id)
            # 只把 questions 关联进来；不写死的话 SQLAlchemy 会把 rubrics 也自动关联掉，
            # 子查询就变成"没有 FROM 的 max(version)"
            .correlate(questions)
            .scalar_subquery()
        )
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    rubrics.c.id.label("rubric_id"),
                    rubrics.c.version,
                    rubrics.c.schema_json,
                    rubrics.c.created_at,
                    questions.c.code.label("question_code"),
                )
                .select_from(rubrics.join(questions, rubrics.c.question_id == questions.c.id))
                .where(rubrics.c.version == latest_version)
                .order_by(questions.c.code)
            ).mappings().all()
        return [
            {
                "rubric_id": row["rubric_id"],
                "version": row["version"],
                "question_code": row["question_code"],
                "stem": row["schema_json"].get("stem", ""),
                "total_score": row["schema_json"].get("total_score", 0),
                "item_count": len(row["schema_json"].get("items") or []),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_rubric(self, rubric_id: int) -> dict[str, Any] | None:
        """按编号打开**某一版**标准，含当时的教师原话与修改意见。"""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(
                    rubrics.c.id.label("rubric_id"),
                    rubrics.c.version,
                    rubrics.c.schema_json.label("rubric"),
                    rubrics.c.checksum,
                    rubrics.c.source_text,
                    rubrics.c.feedback,
                    rubrics.c.created_at,
                    rubrics.c.parent_rubric_id,
                    questions.c.code.label("question_code"),
                )
                .select_from(rubrics.join(questions, rubrics.c.question_id == questions.c.id))
                .where(rubrics.c.id == rubric_id)
            ).mappings().first()
        if row is None:
            return None
        return dict(row)

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
            finalized = self._finalized_answer_ids(conn, answer_ids)
            if finalized:
                raise ValueError(
                    "这些答卷已经定稿，不能重新评分："
                    + "、".join(str(i) for i in finalized)
                    + "（更正需要撤销定稿，当前版本没有该入口）"
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

        "待复核""已定稿"不单独存状态：它们分别由 `route` 与有没有复核记录派生；
        存两份迟早会出现"状态说已评、路由说待复核"的自相矛盾。

        `current` 表示这份任务项指向的聚合结果**是不是该答卷当前最新的结论**。
        同一份答卷被另一个任务重新评分后，本任务仍保留当时那一行（任务级快照），
        但它已经过期：接口要标出来，否则教师会对着一份无法定稿的旧分数找操作入口。
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
                    reviews.c.final_score,
                )
                .select_from(
                    task_items.join(answers, task_items.c.answer_id == answers.c.id)
                    .outerjoin(
                        aggregated_results,
                        task_items.c.aggregate_id == aggregated_results.c.id,
                    )
                    .outerjoin(
                        reviews, reviews.c.aggregated_result_id == aggregated_results.c.id
                    )
                )
                .where(task_items.c.task_id == task_id)
                .order_by(task_items.c.id)
            ).all()

            answer_ids = sorted({row[0] for row in rows})
            latest = {}
            if answer_ids:
                latest = dict(
                    conn.execute(
                        select(
                            aggregated_results.c.answer_id, func.max(aggregated_results.c.id)
                        )
                        .where(aggregated_results.c.answer_id.in_(answer_ids))
                        .group_by(aggregated_results.c.answer_id)
                    ).all()
                )

        return [
            {
                "answer_id": row[0],
                "pseudo_id": row[1],
                "status": row[2],
                "error": row[3],
                "aggregate_id": row[4],
                # 没有聚合结果（pending/running/failed）时无所谓"过期"，算当前
                "current": row[4] is None or latest.get(row[0]) == row[4],
                "score": row[5],
                "max_score": row[6],
                "route": row[7],
                "confidence": row[8],
                "final_score": row[9],
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

    # ------------------------------------------------------------------ 复核

    def _result_conditions(
        self,
        *,
        route: str | None,
        pending: bool,
        question_code: str | None,
    ) -> list:
        """队列与计数共用同一套筛选条件，否则"共 12 份"和列出来的 10 份对不上。"""
        # 待办只看"当前结论"：同一份答卷被重新评分过时，旧聚合结果不再进队列，
        # 否则教师会对一份已经被取代的分数定稿。
        # 已定稿列表相反——它是历史，不该被"最新一次"过滤掉。
        conditions = []
        if pending:
            latest_ids = select(func.max(aggregated_results.c.id)).group_by(
                aggregated_results.c.answer_id
            )
            conditions.append(aggregated_results.c.id.in_(latest_ids))
            conditions.append(reviews.c.id.is_(None))
        else:
            conditions.append(reviews.c.id.is_not(None))
        if route is not None:
            conditions.append(aggregated_results.c.route == route)
        if question_code is not None:
            conditions.append(questions.c.code == question_code)
        return conditions

    def _result_query(self):
        return (
            aggregated_results.join(answers, aggregated_results.c.answer_id == answers.c.id)
            .join(questions, answers.c.question_id == questions.c.id)
            .outerjoin(reviews, reviews.c.aggregated_result_id == aggregated_results.c.id)
        )

    def list_results(
        self,
        *,
        route: str | None = "human",
        pending: bool = True,
        question_code: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列评分结果，默认只给"待人工处理"的那些。

        排序用 `risk`（极差 + 低置信度折算），只在同一批次内可比——它没有理论依据，
        只回答"先看哪一份"。`pending` 不写进表里：有复核记录就是已定稿，
        存一个布尔迟早与 reviews 表对不上。
        """
        conditions = self._result_conditions(
            route=route, pending=pending, question_code=question_code
        )

        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    aggregated_results.c.id,
                    aggregated_results.c.answer_id,
                    answers.c.student_pseudo_id,
                    questions.c.code,
                    aggregated_results.c.raw_score,
                    aggregated_results.c.max_score,
                    aggregated_results.c.route,
                    aggregated_results.c.risk,
                    aggregated_results.c.confidence,
                    aggregated_results.c.dispersion,
                    aggregated_results.c.total_range,
                    aggregated_results.c.grounding_rate,
                    aggregated_results.c.reasons_json,
                    aggregated_results.c.created_at,
                    reviews.c.id,
                    reviews.c.final_score,
                )
                .select_from(self._result_query())
                .where(*conditions)
                # risk 只在一份标准内可比（架构 §2.2），所以先按题号分组、组内按 risk 排。
                # 跨题混排再按 risk 排会把"不可比"的数字放在一起比较。
                .order_by(
                    questions.c.code,
                    aggregated_results.c.risk.desc(),
                    aggregated_results.c.id,
                )
                .limit(limit)
                .offset(offset)
            ).all()
        return [
            {
                "aggregate_id": row[0],
                "answer_id": row[1],
                "pseudo_id": row[2],
                "question_code": row[3],
                "raw_score": row[4],
                "final_score": row[15],
                "max_score": row[5],
                "route": row[6],
                "risk": row[7],
                "confidence": row[8],
                "dispersion": row[9],
                "total_range": row[10],
                "grounding_rate": row[11],
                "reasons": list(row[12]),
                "created_at": row[13],
                "reviewed": row[14] is not None,
            }
            for row in rows
        ]

    def count_results(
        self,
        *,
        route: str | None = "human",
        pending: bool = True,
        question_code: str | None = None,
    ) -> int:
        """队列总数。与 `list_results` 用同一套条件，只是不取明细。"""
        conditions = self._result_conditions(
            route=route, pending=pending, question_code=question_code
        )
        with self._engine.connect() as conn:
            return conn.execute(
                select(func.count()).select_from(self._result_query()).where(*conditions)
            ).scalar_one()

    def latest_aggregate_id(self, answer_id: int) -> int | None:
        """该答卷最近一次评分的聚合结果号；详情页用它判断"是否已被取代"。"""
        with self._engine.connect() as conn:
            return conn.execute(
                select(func.max(aggregated_results.c.id)).where(
                    aggregated_results.c.answer_id == answer_id
                )
            ).scalar_one_or_none()

    def load_review(self, aggregate_id: int) -> ReviewRecord | None:
        with self._engine.connect() as conn:
            return self._review_of(conn, aggregate_id)

    def record_review(
        self,
        aggregate_id: int,
        *,
        decision: str,
        final_score: float | None = None,
        comment: str = "",
        reviewer_id: str = "admin",
    ) -> ReviewRecord:
        """给一份结果定稿：采纳原分或改分。

        最终分写进 `reviews`，**不改写** `aggregated_results.raw_score`：
        模型当时给了多少是一份证据，人工改成多少是另一份证据，两份都要留着。
        """
        if decision not in REVIEW_DECISIONS:
            raise ReviewRejected("decision 只能是 accept 或 override")
        comment = comment.strip()
        if decision == "override" and not comment:
            raise ReviewRejected("改分必须写明理由：没有意见的改分无法解释")

        with self._engine.begin() as conn:
            # FOR UPDATE 与 save_evaluation_for_answer 抢同一行，见那边的注释。
            aggregate = conn.execute(
                select(
                    aggregated_results.c.raw_score,
                    aggregated_results.c.max_score,
                    aggregated_results.c.answer_id,
                )
                .where(aggregated_results.c.id == aggregate_id)
                .with_for_update()
            ).first()
            if aggregate is None:
                raise ResultNotFound(f"评分结果不存在：{aggregate_id}")
            self._require_current(conn, aggregate_id, aggregate[2])
            if self._review_of(conn, aggregate_id) is not None:
                raise ResultFinalized("这份结果已经定稿，不能重复复核")

            final = _reviewed_score(
                decision=decision,
                given=final_score,
                raw=aggregate[0],
                max_score=aggregate[1],
            )
            try:
                conn.execute(
                    reviews.insert().values(
                        aggregated_result_id=aggregate_id,
                        reviewer_id=reviewer_id,
                        final_score=final,
                        decision=decision,
                        comment=comment,
                    )
                )
            except IntegrityError as exc:
                # 唯一索引兜底：并发重复提交时，第二次在这里被挡住。
                # 这条 INSERT 上只有 uq_reviews_aggregate 一个约束（外键目标已在上方锁定并查到），
                # 所以 IntegrityError 在这里就只可能是"已经定稿"。
                raise ResultFinalized("这份结果已经定稿，不能重复复核") from exc
            inserted = self._review_of(conn, aggregate_id)
        if inserted is None:  # pragma: no cover - 刚插进去就没了说明库坏了
            raise ResultNotFound(f"评分结果不存在：{aggregate_id}")
        return inserted

    def accept_high_confidence(
        self,
        aggregate_ids: list[int],
        *,
        comment: str = "",
        reviewer_id: str = "admin",
    ) -> list[ReviewRecord]:
        """批量通过自动路由的结果。

        **只接受 `route == 'auto'` 的**：转人工的结果必须逐份看，
        否则"批量通过"就成了绕过路由的后门。整批是一个事务——
        混进一份不合格的，整批不落库，避免"通过了一半"这种说不清的状态。
        """
        ids = list(dict.fromkeys(aggregate_ids))
        if not ids:
            raise ReviewRejected("至少要选一份结果")

        with self._engine.begin() as conn:
            # 与单份复核、重评抢同一批行：批量通过也要在锁内做"是否过期"的判断，
            # 否则检查通过之后、插入之前，另一条重评可以插进更新的聚合结果。
            rows = conn.execute(
                select(
                    aggregated_results.c.id,
                    aggregated_results.c.raw_score,
                    aggregated_results.c.route,
                    aggregated_results.c.answer_id,
                )
                .where(aggregated_results.c.id.in_(ids))
                .with_for_update()
            ).all()
            found = {row[0]: row for row in rows}
            missing = [str(aggregate_id) for aggregate_id in ids if aggregate_id not in found]
            if missing:
                raise ReviewRejected("这些评分结果不存在：" + "、".join(missing))

            needs_review = [
                str(aggregate_id) for aggregate_id in ids if found[aggregate_id][2] != "auto"
            ]
            if needs_review:
                raise ReviewRejected(
                    "这些结果需要逐份复核，不能批量通过：" + "、".join(needs_review)
                )

            for aggregate_id in ids:
                self._require_current(conn, aggregate_id, found[aggregate_id][3])

            finalized = conn.execute(
                select(reviews.c.aggregated_result_id).where(
                    reviews.c.aggregated_result_id.in_(ids)
                )
            ).scalars().all()
            if finalized:
                raise ResultFinalized(
                    "这些结果已经定稿：" + "、".join(str(item) for item in finalized)
                )

            try:
                conn.execute(
                    reviews.insert(),
                    [
                        {
                            "aggregated_result_id": aggregate_id,
                            "reviewer_id": reviewer_id,
                            "final_score": round(found[aggregate_id][1], 2),
                            "decision": "accept",
                            "comment": comment.strip(),
                        }
                        for aggregate_id in ids
                    ],
                )
            except IntegrityError as exc:
                # 与单份复核同理：唯一索引兜底，并发时整批回滚而不是只进一半
                raise ResultFinalized("这些结果中有已经定稿的，请刷新后重试") from exc
            inserted = conn.execute(
                select(reviews)
                .where(reviews.c.aggregated_result_id.in_(ids))
                .order_by(reviews.c.id)
            ).mappings().all()
        return [_review_record(row) for row in inserted]

    def _finalized_answer_ids(self, conn, answer_ids: list[int]) -> list[int]:
        """这些答卷里，哪些已经有定稿记录（不看是哪一次结论）。"""
        if not answer_ids:
            return []
        return conn.execute(
            select(aggregated_results.c.answer_id)
            .distinct()
            .select_from(
                reviews.join(
                    aggregated_results,
                    reviews.c.aggregated_result_id == aggregated_results.c.id,
                )
            )
            .where(aggregated_results.c.answer_id.in_(answer_ids))
        ).scalars().all()

    def _require_current(self, conn, aggregate_id: int, answer_id: int) -> None:
        latest = conn.execute(
            select(func.max(aggregated_results.c.id)).where(
                aggregated_results.c.answer_id == answer_id
            )
        ).scalar_one()
        if latest != aggregate_id:
            raise ResultSuperseded(
                "这份结果已被重新评分取代，请复核最新一次结果"
            )

    def _review_of(self, conn, aggregate_id: int) -> ReviewRecord | None:
        row = conn.execute(
            select(reviews).where(reviews.c.aggregated_result_id == aggregate_id)
        ).mappings().first()
        return None if row is None else _review_record(row)

    # ------------------------------------------------------------------ 读取

    def load_evaluation(
        self,
        *,
        answer_id: int | None = None,
        run_id: int | None = None,
        aggregate_id: int | None = None,
    ) -> StoredEvaluation | None:
        """按答卷号、运行号或聚合结果号读回完整结果。

        - 按答卷号：取该答卷**最新**的聚合结果（重评一次就多一行，最新即当前结论）。
        - 按运行号：取**包含这一次运行**的那次聚合。不能图省事取最新——重评之后，
          这条运行和最新结论已经不是一回事了，硬拿最新的会把两次评分混成一次。
        - 按聚合结果号：复核详情用。复核是针对**某一次结论**的，不能拿"该答卷最新一次"
          顶替——那份可能已经是另一次重评了。
        """
        if sum(item is not None for item in (answer_id, run_id, aggregate_id)) != 1:
            raise ValueError("answer_id、run_id 与 aggregate_id 必须且只能给一个")

        with self._engine.connect() as conn:
            aggregate_row = None
            if aggregate_id is not None:
                aggregate_row = conn.execute(
                    select(aggregated_results).where(
                        aggregated_results.c.id == aggregate_id
                    )
                ).mappings().first()
                if aggregate_row is None:
                    return None
                answer_id = aggregate_row["answer_id"]
            elif run_id is not None:
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

            if aggregate_row is None:
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
