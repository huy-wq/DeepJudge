"""复核定稿唯一索引

一份评分结果只允许有一条复核记录（"定稿"是一次性的）。
把原来的普通索引换成唯一索引：既保住按结果查复核的速度，
也让并发重复提交由数据库挡住，而不是只靠"先查再插"的窗口。

Revision ID: 09c6b9dacb45
Revises: 9d5fdab5c0a5
Create Date: 2026-10-27 22:57:53.077748
"""

from __future__ import annotations

from alembic import op


revision = '09c6b9dacb45'
down_revision = '9d5fdab5c0a5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 顺序不能反：aggregated_result_id 上有外键，MySQL 不允许把该列唯一的索引删掉，
    # 否则会报 "Cannot drop index ... needed in a foreign key constraint"。
    # 先建唯一索引，外键就有了新的支撑索引，再删旧的普通索引才成立。
    # 之前 reviews 表没有任何写入路径（复核接口本票才落地），不会撞已存在的重复行。
    op.create_index(
        'uq_reviews_aggregate', 'reviews', ['aggregated_result_id'], unique=True
    )
    op.drop_index('ix_reviews_aggregate', table_name='reviews')


def downgrade() -> None:
    op.create_index('ix_reviews_aggregate', 'reviews', ['aggregated_result_id'], unique=False)
    op.drop_index('uq_reviews_aggregate', table_name='reviews')
