"""迁移与模型的等价性测试。

`app/store/tables.py` 是表结构的**代码**真源，Alembic 迁移是**部署**真源。
两者一旦漂移，就会出现"测试库建得起来、生产库缺列"这种只在部署时炸的问题，
所以这里真的执行一遍 upgrade/downgrade，再把迁移出来的结构与 metadata 逐项对照。
"""

import tempfile
import unittest
from pathlib import Path

try:
    import alembic  # noqa: F401
    import sqlalchemy  # noqa: F401
except ImportError:  # pragma: no cover
    raise unittest.SkipTest(
        "未安装 alembic/sqlalchemy，先执行：python3 -m venv .venv && .venv/bin/pip install -e backend"
    )

from alembic import command
from alembic.config import Config
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, create_engine, inspect

from app.store.tables import metadata
from tests.support import BACKEND_DIR, database_url


def describe_metadata() -> dict:
    described = {}
    for name, table in metadata.tables.items():
        described[name] = {
            "columns": {(c.name, bool(c.nullable)) for c in table.columns},
            # 带上 unique 标志：唯一索引与非唯一索引承担完全不同的约束，
            # 只比名字的话"唯一性没建出来"也能通过
            "indexes": {(index.name, bool(index.unique)) for index in table.indexes},
            "uniques": {
                tuple(sorted(column.name for column in constraint.columns))
                for constraint in table.constraints
                if isinstance(constraint, UniqueConstraint)
            },
            "foreign_keys": {
                (
                    tuple(element.parent.name for element in constraint.elements),
                    tuple(element.column.table.name for element in constraint.elements),
                )
                for constraint in table.constraints
                if isinstance(constraint, ForeignKeyConstraint)
            },
        }
    return described


def describe_database(url: str) -> dict:
    engine = create_engine(url)
    inspector = inspect(engine)
    described = {}
    for name in inspector.get_table_names():
        if name == "alembic_version":
            continue
        described[name] = {
            "columns": {(c["name"], bool(c["nullable"])) for c in inspector.get_columns(name)},
            "indexes": {
                (i["name"], bool(i.get("unique"))) for i in inspector.get_indexes(name)
            },
            "uniques": {
                tuple(sorted(c["column_names"]))
                for c in inspector.get_unique_constraints(name)
                if None not in c["column_names"]
            },
            "foreign_keys": {
                (
                    tuple(fk["constrained_columns"]),
                    tuple([fk["referred_table"]]),
                )
                for fk in inspector.get_foreign_keys(name)
            },
        }
    engine.dispose()
    return described


class MigrationMatchesMetadata(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.url = f"sqlite:///{Path(self.tempdir.name) / 'migrate.db'}"
        self.config = Config(str(BACKEND_DIR / "alembic.ini"))
        self.config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
        # 不打 alembic 的 INFO 日志：会把断言失败的信息挤掉
        self.config.config_file_name = None
        # 让 env.py 读到的 URL 与这里一致；context 退出时自动恢复
        env = database_url(self.url)
        env.__enter__()
        self.addCleanup(lambda: env.__exit__(None, None, None))

    def test_upgrade_creates_every_table_and_column(self):
        command.upgrade(self.config, "head")

        self.assertEqual(describe_database(self.url), describe_metadata())

    def test_downgrade_removes_every_table(self):
        command.upgrade(self.config, "head")
        command.downgrade(self.config, "base")

        engine = create_engine(self.url)
        self.assertEqual(inspect(engine).get_table_names(), ["alembic_version"])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
