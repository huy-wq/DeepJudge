"""答卷导入所需字段

Revision ID: 48cfbde49d0b
Revises: 371541d1c60c
Create Date: 2026-10-27 22:09:27.593117
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '48cfbde49d0b'
down_revision = '371541d1c60c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 两个新列都带 server_default：已经有数据的库里，旧行必须能补上值，
    # 否则 ALTER TABLE 会直接失败。content_hash 的空串只可能出现在本次迁移之前的行。
    op.add_column('answers', sa.Column('content_hash', sa.String(length=64), server_default='', nullable=False, comment='规范化文本的 sha256，查重用；空串只可能出现在本列加入之前的历史行'))
    op.add_column('answers', sa.Column('source', sa.String(length=16), server_default='paste', nullable=False, comment='csv / paste：答卷是怎么进来的（架构 §5.7 预留字段）'))
    op.create_index('ix_answers_content', 'answers', ['question_id', 'content_hash'], unique=False)


def downgrade() -> None:
    # 两个新列都带 server_default：已经有数据的库里，旧行必须能补上值，
    # 否则 ALTER TABLE 会直接失败。content_hash 的空串只可能出现在本次迁移之前的行。
    op.drop_index('ix_answers_content', table_name='answers')
    op.drop_column('answers', 'source')
    op.drop_column('answers', 'content_hash')
