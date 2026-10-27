"""存储表定义（架构 §4.4 的物理落地）。

只描述**逻辑模型**，不写 `CREATE TABLE`：
- 方言差异用 `with_variant` 收口（MySQL 用 LONGTEXT / DOUBLE / JSON，其它方言退回通用类型），
  这样测试可以用内存 SQLite 跑，生产仍是 MySQL 8；
- 每张表都声明 `utf8mb4`，中文与 emoji 都是"字面意义上的字符"；
- 时间戳一律由数据库给默认值，避免客户端时钟不一致时排序错乱。
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import mysql

metadata = MetaData()

# MySQL 8 的 utf8mb4 是唯一能完整存下 emoji 的字符集；collation 用 8.0 默认值。
MYSQL_TABLE_ARGS = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
    "mysql_engine": "InnoDB",
}

# 答卷与证据都是不限长的中文文本，MySQL 的 TEXT 只有 64KB，必须用 LONGTEXT。
LongText = Text().with_variant(mysql.LONGTEXT(), "mysql")
# 分值全靠业务层 round(2)，用浮点即可。MySQL 用 DOUBLE 且显式 asdecimal=False：
# 否则查询回来是 Decimal，与领域对象的 float 不相等，"字段无损"立刻破功。
Score = Float().with_variant(mysql.DOUBLE(asdecimal=False), "mysql")
# 0~1 的比值（离散度、置信度、接地率）与分值同类型，另起名字只为读起来明确
Rate = Score

questions = Table(
    "questions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(64), nullable=False, unique=True, comment="业务编号，如 Q-01"),
    Column("stem", LongText, nullable=False),
    Column("type", String(32), nullable=False, server_default="short_answer"),
    Column("total_score", Score, nullable=False),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    **MYSQL_TABLE_ARGS,
)

rubrics = Table(
    "rubrics",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "question_id",
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("version", Integer, nullable=False),
    Column("schema_json", JSON, nullable=False, comment="当时的标准全文，只增不改"),
    Column("checksum", String(64), nullable=False, comment="schema_json 的 sha256"),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    UniqueConstraint("question_id", "version", name="uq_rubrics_question_version"),
    Index("ix_rubrics_question_created", "question_id", "created_at"),
    **MYSQL_TABLE_ARGS,
)

answers = Table(
    "answers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "question_id",
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("student_pseudo_id", String(64), nullable=False, server_default="anon"),
    Column("raw_text", LongText, nullable=False),
    Column("normalized_text", LongText, nullable=False, comment="规范化后的文本，供检索与审计；证据偏移基于原文"),
    Column(
        "content_hash",
        String(64),
        nullable=False,
        server_default="",
        comment="规范化文本的 sha256，查重用；空串只可能出现在本列加入之前的历史行",
    ),
    Column(
        "source",
        String(16),
        nullable=False,
        server_default="paste",
        comment="csv / paste：答卷是怎么进来的（架构 §5.7 预留字段）",
    ),
    Column("truncated", Boolean, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Index("ix_answers_question", "question_id"),
    Index("ix_answers_content", "question_id", "content_hash"),
    **MYSQL_TABLE_ARGS,
)

grading_runs = Table(
    "grading_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "answer_id",
        Integer,
        ForeignKey("answers.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "rubric_id",
        Integer,
        ForeignKey("rubrics.id", ondelete="RESTRICT"),
        nullable=False,
        comment="指向不可变的标准快照，旧运行永远能显示当时的标准",
    ),
    Column("prompt_version", String(32), nullable=False, server_default="v1"),
    Column("model", String(64), nullable=False, server_default=""),
    Column("params_json", JSON, nullable=False),
    Column("status", String(16), nullable=False, server_default="ok"),
    Column("score", Score, nullable=True, comment="服务端算分结果，失败时为 NULL"),
    Column("comment", LongText, nullable=False),
    Column("tokens_in", Integer, nullable=True),
    Column("tokens_out", Integer, nullable=True),
    Column("latency_ms", Integer, nullable=True),
    Column("error", String(255), nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Index("ix_runs_answer", "answer_id"),
    Index("ix_runs_rubric", "rubric_id"),
    **MYSQL_TABLE_ARGS,
)

item_results = Table(
    "item_results",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "run_id",
        Integer,
        ForeignKey("grading_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", String(16), nullable=False, comment="item 采分点 / deduction 扣分项"),
    Column("rubric_item_id", String(64), nullable=False),
    Column("level", Score, nullable=True, comment="扣分项没有等级"),
    Column("hit", Boolean, nullable=False, server_default="0"),
    Column("evidence", LongText, nullable=False),
    Column("span_start", Integer, nullable=True),
    Column("span_end", Integer, nullable=True),
    Column("grounded", Boolean, nullable=False, server_default="0"),
    Column("reason", LongText, nullable=False),
    Index("ix_item_results_run", "run_id"),
    **MYSQL_TABLE_ARGS,
)

aggregated_results = Table(
    "aggregated_results",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "answer_id",
        Integer,
        ForeignKey("answers.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "rubric_id",
        Integer,
        ForeignKey("rubrics.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("sample_run_ids", JSON, nullable=False, comment="参与聚合的运行编号，按采样顺序"),
    Column("levels_json", JSON, nullable=False),
    Column("sample_totals_json", JSON, nullable=False),
    Column("raw_score", Score, nullable=False),
    Column("calibrated_score", Score, nullable=True, comment="校准上线前恒为 NULL"),
    Column("max_score", Score, nullable=False),
    Column("dispersion", Rate, nullable=False),
    Column("total_range", Rate, nullable=False),
    Column("confidence", Rate, nullable=False),
    Column("grounding_rate", Rate, nullable=False),
    Column("route", String(8), nullable=False),
    Column("risk", Rate, nullable=False),
    Column("reasons_json", JSON, nullable=False),
    Column("applied_deductions_json", JSON, nullable=False),
    Column("calibration_version", String(32), nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Index("ix_aggregates_answer", "answer_id"),
    **MYSQL_TABLE_ARGS,
)

reviews = Table(
    "reviews",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "aggregated_result_id",
        Integer,
        ForeignKey("aggregated_results.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("reviewer_id", String(64), nullable=False, server_default="admin"),
    Column("final_score", Score, nullable=False),
    Column("decision", String(16), nullable=False, comment="accept 采纳 / override 改分"),
    Column("comment", LongText, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Index("ix_reviews_aggregate", "aggregated_result_id"),
    **MYSQL_TABLE_ARGS,
)

calibration_models = Table(
    "calibration_models",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("version", String(32), nullable=False, unique=True),
    Column("method", String(32), nullable=False),
    Column("params_json", JSON, nullable=False),
    Column("train_size", Integer, nullable=False, server_default="0"),
    Column("metrics_json", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    **MYSQL_TABLE_ARGS,
)
