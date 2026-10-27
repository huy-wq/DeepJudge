"""批量评分任务

Revision ID: 9d5fdab5c0a5
Revises: 48cfbde49d0b
Create Date: 2026-10-27 22:44:49.585944
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '9d5fdab5c0a5'
down_revision = '48cfbde49d0b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('grading_tasks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('rubric_id', sa.Integer(), nullable=False, comment='任务启动时固定下来的标准快照：之后改标准不影响本批次'),
    sa.Column('samples', sa.Integer(), server_default='3', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False, comment='pending / running / done / interrupted'),
    sa.Column('total', sa.Integer(), server_default='0', nullable=False),
    sa.Column('done', sa.Integer(), server_default='0', nullable=False),
    sa.Column('failed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error', sa.String(length=255), nullable=True, comment='任务级失败原因（执行器崩溃、进程中断）；单份失败记在 task_items.error'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rubric_id'], ['rubrics.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_ai_ci',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_tasks_question', 'grading_tasks', ['question_id'], unique=False)
    op.create_index('ix_tasks_status', 'grading_tasks', ['status'], unique=False)
    op.create_table('task_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('answer_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False, comment='pending / running / graded / failed'),
    sa.Column('error', sa.String(length=255), nullable=True),
    sa.Column('aggregate_id', sa.Integer(), nullable=True, comment='评完后指向聚合结果；待复核由该行的 route 派生，不在本表另存'),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['aggregate_id'], ['aggregated_results.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['answer_id'], ['answers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['grading_tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'answer_id', name='uq_task_items_task_answer'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_0900_ai_ci',
    mysql_engine='InnoDB'
    )
    op.create_index('ix_task_items_task_status', 'task_items', ['task_id', 'status'], unique=False)


def downgrade() -> None:
    # 先删子表再删父表：外键反向会失败
    op.drop_index('ix_task_items_task_status', table_name='task_items')
    op.drop_table('task_items')
    op.drop_index('ix_tasks_status', table_name='grading_tasks')
    op.drop_index('ix_tasks_question', table_name='grading_tasks')
    op.drop_table('grading_tasks')
