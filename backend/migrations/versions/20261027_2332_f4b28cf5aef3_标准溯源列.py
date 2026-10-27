"""标准溯源列

`rubrics` 每一行都是一份不可变快照；新增两列记录这份快照是怎么来的：
教师口述的原文（`source_text`）与生成这一版时的修改意见（`feedback`）。
没有它们，答辩被问"这个权重怎么来的"就只能翻聊天记录。

Revision ID: f4b28cf5aef3
Revises: 09c6b9dacb45
Create Date: 2026-10-27 23:32:11.025288
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = 'f4b28cf5aef3'
down_revision = '09c6b9dacb45'
branch_labels = None
depends_on = None

LONGTEXT = sa.Text().with_variant(mysql.LONGTEXT(), 'mysql')


def upgrade() -> None:
    # server_default 让已有行直接补上空串：ALTER TABLE 不会因为 NOT NULL 失败。
    # 空串的语义是"这份标准不是通过生成流程保存的"，不是"原文为空"。
    # MySQL 8 的 TEXT 不接受裸字面量默认值，必须写成表达式 `DEFAULT ('')`；
    # SQLite 也接受括号写法。
    op.add_column(
        'rubrics',
        sa.Column(
            'source_text',
            LONGTEXT,
            server_default=sa.text("('')"),
            nullable=False,
            comment='教师口述的评分标准原文：这一版是怎么来的',
        ),
    )
    op.add_column(
        'rubrics',
        sa.Column(
            'feedback',
            LONGTEXT,
            server_default=sa.text("('')"),
            nullable=False,
            comment='生成这一版时的修改意见；空串表示首次保存或手工编辑',
        ),
    )
    # 自引用但**不加外键**：标准只增不删，父版本不会消失；SQLite 上加外键要重建整表。
    op.add_column(
        'rubrics',
        sa.Column(
            'parent_rubric_id',
            sa.Integer(),
            nullable=True,
            comment='这一版是从哪一版改出来的；首次保存为空',
        ),
    )


def downgrade() -> None:
    op.drop_column('rubrics', 'parent_rubric_id')
    op.drop_column('rubrics', 'feedback')
    op.drop_column('rubrics', 'source_text')
