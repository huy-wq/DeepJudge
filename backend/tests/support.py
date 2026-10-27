"""测试基建。

两件事：

1. `create_schema`：直接用 `metadata` 建表，**不走迁移**。用于那些不关心迁移的用例
   （每次省下约 0.5 秒×8 张表）。迁移与模型是否对齐，由 `tests/test_migrations.py` 单独负责。
2. `upgrade_database` / `database_url`：需要**真迁移**的用例（HTTP 接口那一层）用它，
   这样测的是"部署出来的表结构"而不是"测试顺手建的表"。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.store import Store
from app.store import metadata as schema

BACKEND_DIR = Path(__file__).resolve().parents[1]


def create_schema(store: Store) -> None:
    schema.create_all(store.engine)


@contextmanager
def database_url(url: str) -> Iterator[str]:
    """临时把 DATABASE_URL 指向测试库，退出时恢复。

    `create_app` 与 Alembic 都从这个变量读库地址，所以必须围着它们设。
    """
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        yield url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def upgrade_database(url: str) -> None:
    """把一个空库升级到最新表结构。

    跳过 `fileConfig`：alembic.ini 里配了 INFO 级日志，每建一次库就打一屏
    "Running upgrade"，把真正的失败信息挤没了。
    """
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.config_file_name = None
    with database_url(url):
        command.upgrade(config, "head")
