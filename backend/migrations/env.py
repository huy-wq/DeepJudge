"""Alembic 运行环境。

数据库地址只从环境变量 `DATABASE_URL` 读（会先加载项目 .env），
不在 alembic.ini 里写口令；`target_metadata` 直接指向 `app/store/tables.py`，
这样 `alembic revision --autogenerate` 与手写迁移始终对照同一份模型。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import load_project_env  # noqa: E402
from app.store import metadata  # noqa: E402

load_project_env(BACKEND_DIR)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "未设置 DATABASE_URL。示例：\n"
            "  export DATABASE_URL='mysql+pymysql://deepjudge:deepjudge@127.0.0.1:3306/deepjudge?charset=utf8mb4'"
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
