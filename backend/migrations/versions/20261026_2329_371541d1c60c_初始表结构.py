"""初始表结构

Revision ID: 371541d1c60c
Revises: 
Create Date: 2026-10-26 23:29:07.983091
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = '371541d1c60c'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 由 alembic autogenerate 生成，随后手工校正两处：
    # 1. 默认时间戳改成跨方言都合法的 CURRENT_TIMESTAMP（autogenerate 反射出的是 SQLite 写法）；
    # 2. 浮点显式 asdecimal=False —— 否则 MySQL 查询回来是 Decimal，与领域对象不相等。
    op.create_table('calibration_models',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('version', sa.String(length=32), nullable=False),
    sa.Column('method', sa.String(length=32), nullable=False),
    sa.Column('params_json', sa.JSON(), nullable=False),
    sa.Column('train_size', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('metrics_json', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_ai_ci',
    mysql_engine='InnoDB'
    )
    op.create_table('questions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False, comment='业务编号，如 Q-01'),
    sa.Column('stem', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('type', sa.String(length=32), server_default='short_answer', nullable=False),
    sa.Column('total_score', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_ai_ci',
    mysql_engine='InnoDB'
    )
    op.create_table('answers',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('student_pseudo_id', sa.String(length=64), server_default='anon', nullable=False),
    sa.Column('raw_text', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('normalized_text', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False, comment='规范化后的文本，供检索与审计；证据偏移基于原文'),
    sa.Column('truncated', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_ai_ci',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_answers_question', 'answers', ['question_id'], unique=False)
    op.create_table('rubrics',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('schema_json', sa.JSON(), nullable=False, comment='当时的标准全文，只增不改'),
    sa.Column('checksum', sa.String(length=64), nullable=False, comment='schema_json 的 sha256'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('question_id', 'version', name='uq_rubrics_question_version'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_ai_ci',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_rubrics_question_created', 'rubrics', ['question_id', 'created_at'], unique=False)
    op.create_table('aggregated_results',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('answer_id', sa.Integer(), nullable=False),
    sa.Column('rubric_id', sa.Integer(), nullable=False),
    sa.Column('sample_run_ids', sa.JSON(), nullable=False, comment='参与聚合的运行编号，按采样顺序'),
    sa.Column('levels_json', sa.JSON(), nullable=False),
    sa.Column('sample_totals_json', sa.JSON(), nullable=False),
    sa.Column('raw_score', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=False),
    sa.Column('calibrated_score', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=True, comment='校准上线前恒为 NULL'),
    sa.Column('max_score', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=False),
    sa.Column('dispersion', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=False),
    sa.Column('total_range', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=False),
    sa.Column('confidence', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=False),
    sa.Column('grounding_rate', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=False),
    sa.Column('route', sa.String(length=8), nullable=False),
    sa.Column('risk', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=False),
    sa.Column('reasons_json', sa.JSON(), nullable=False),
    sa.Column('applied_deductions_json', sa.JSON(), nullable=False),
    sa.Column('calibration_version', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['answer_id'], ['answers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rubric_id'], ['rubrics.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_ai_ci',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_aggregates_answer', 'aggregated_results', ['answer_id'], unique=False)
    op.create_table('grading_runs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('answer_id', sa.Integer(), nullable=False),
    sa.Column('rubric_id', sa.Integer(), nullable=False, comment='指向不可变的标准快照，旧运行永远能显示当时的标准'),
    sa.Column('prompt_version', sa.String(length=32), server_default='v1', nullable=False),
    sa.Column('model', sa.String(length=64), server_default='', nullable=False),
    sa.Column('params_json', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='ok', nullable=False),
    sa.Column('score', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=True, comment='服务端算分结果，失败时为 NULL'),
    sa.Column('comment', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('tokens_in', sa.Integer(), nullable=True),
    sa.Column('tokens_out', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('error', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['answer_id'], ['answers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rubric_id'], ['rubrics.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_ai_ci',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_runs_answer', 'grading_runs', ['answer_id'], unique=False)
    op.create_index('ix_runs_rubric', 'grading_runs', ['rubric_id'], unique=False)
    op.create_table('item_results',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False, comment='item 采分点 / deduction 扣分项'),
    sa.Column('rubric_item_id', sa.String(length=64), nullable=False),
    sa.Column('level', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=True, comment='扣分项没有等级'),
    sa.Column('hit', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('evidence', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('span_start', sa.Integer(), nullable=True),
    sa.Column('span_end', sa.Integer(), nullable=True),
    sa.Column('grounded', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('reason', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['grading_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_ai_ci',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_item_results_run', 'item_results', ['run_id'], unique=False)
    op.create_table('reviews',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('aggregated_result_id', sa.Integer(), nullable=False),
    sa.Column('reviewer_id', sa.String(length=64), server_default='admin', nullable=False),
    sa.Column('final_score', sa.Float().with_variant(mysql.DOUBLE(asdecimal=False), 'mysql'), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False, comment='accept 采纳 / override 改分'),
    sa.Column('comment', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['aggregated_result_id'], ['aggregated_results.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_ai_ci',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_reviews_aggregate', 'reviews', ['aggregated_result_id'], unique=False)


def downgrade() -> None:
    # 由 alembic autogenerate 生成，随后手工校正两处：
    # 1. 默认时间戳改成跨方言都合法的 CURRENT_TIMESTAMP（autogenerate 反射出的是 SQLite 写法）；
    # 2. 浮点显式 asdecimal=False —— 否则 MySQL 查询回来是 Decimal，与领域对象不相等。
    op.drop_index('ix_reviews_aggregate', table_name='reviews')
    op.drop_table('reviews')
    op.drop_index('ix_item_results_run', table_name='item_results')
    op.drop_table('item_results')
    op.drop_index('ix_runs_rubric', table_name='grading_runs')
    op.drop_index('ix_runs_answer', table_name='grading_runs')
    op.drop_table('grading_runs')
    op.drop_index('ix_aggregates_answer', table_name='aggregated_results')
    op.drop_table('aggregated_results')
    op.drop_index('ix_rubrics_question_created', table_name='rubrics')
    op.drop_table('rubrics')
    op.drop_index('ix_answers_question', table_name='answers')
    op.drop_table('answers')
    op.drop_table('questions')
    op.drop_table('calibration_models')
