"""测试基建。

建表不经过 `Store`：存储层的公开接口只有"存一次评分"和"按编号读回"，
不该再长出一个绕过 Alembic 的建表入口。测试是工具层，直接拿 `metadata` 建表即可，
每个用例省下约 0.5 秒的迁移开销；`tests/test_migrations.py` 另有用例保证
迁移建出来的表与这份 `metadata` 完全一致。
"""

from __future__ import annotations

from app.store import Store
from app.store import metadata as schema


def create_schema(store: Store) -> None:
    schema.create_all(store.engine)
