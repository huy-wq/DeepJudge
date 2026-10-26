"""MySQL 8 真库验证：字符集、方言类型与迁移 DDL。

默认跳过。要跑它，先起库再给一个有 CREATE DATABASE 权限的连接地址：

    ./scripts/dev-db.sh up
    DEEPJUDGE_TEST_MYSQL_URL='mysql+pymysql://deepjudge:deepjudge@127.0.0.1:3307/deepjudge?charset=utf8mb4' \\
        .venv/bin/python -m unittest tests.test_store_mysql

用例**每次新建一个临时库、跑完删掉**，不碰你给的那个库里的数据：
"测试会不会把开发库清空"这种事，不该靠人记得别在生产地址上跑测试来避免。

SQLite 跑得快，但它不校验字符集、也接受任何类型——"中文与 emoji 存得下"这一条只能在真库上证明。
"""

import json
import os
import unittest
import uuid
from pathlib import Path

try:
    import alembic  # noqa: F401
    import pymysql  # noqa: F401
    import sqlalchemy  # noqa: F401
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("未安装 pymysql/sqlalchemy/alembic，见 backend/pyproject.toml")

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.rubric import from_dict
from app.pipeline import evaluate
from app.providers.fake import CannedProvider
from app.store import RunMeta, Store
from tests.fixtures import ANSWER, RUBRIC_DATA, payload_text

MYSQL_URL = os.environ.get("DEEPJUDGE_TEST_MYSQL_URL", "").strip()

# 中文、emoji（4 字节）、数学符号：任何一处按 latin1 或 utf8mb3 建表都会在这条用例上炸掉。
EMOJI_ANSWER = ANSWER + "补充：三次握手 🤝 建立连接 ✅ ℃"
EMOJI_EVIDENCE = "三次握手 🤝 建立连接"
EMOJI_COMMENT = "过程正确 👍，第二次握手的目的可以再写清楚些。"


def emoji_rubric_data() -> dict:
    data = json.loads(json.dumps(RUBRIC_DATA))
    data["stem"] = "简述 TCP 三次握手 🚀 的过程，并说明其目的。"
    data["items"][0]["desc"] = "客户端发送 SYN 及初始序列号（🎯 关键点）"
    return data


def emoji_payload() -> str:
    data = json.loads(payload_text())
    data["items"][2]["evidence"] = EMOJI_EVIDENCE
    data["comment"] = EMOJI_COMMENT
    return json.dumps(data, ensure_ascii=False)


@unittest.skipUnless(MYSQL_URL, "未设置 DEEPJUDGE_TEST_MYSQL_URL，跳过 MySQL 真库验证")
class MySQLStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        backend = Path(__file__).resolve().parents[1]
        cls.config = Config(str(backend / "alembic.ini"))
        cls.config.set_main_option("script_location", str(backend / "migrations"))

        base = make_url(MYSQL_URL)
        cls.database = f"deepjudge_test_{uuid.uuid4().hex[:8]}"
        try:
            admin = create_engine(base.set(database=None), isolation_level="AUTOCOMMIT")
            with admin.connect() as conn:
                conn.execute(
                    text(
                        f"CREATE DATABASE `{cls.database}` "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                    )
                )
            admin.dispose()
        except Exception as exc:  # pragma: no cover - 环境问题，不是被测代码的问题
            raise unittest.SkipTest(
                f"无法创建临时库（需要 CREATE DATABASE 权限）：{exc}"
            ) from exc

        cls.url = base.set(database=cls.database).render_as_string(hide_password=False)
        os.environ["DATABASE_URL"] = cls.url
        command.upgrade(cls.config, "head")
        cls.store = Store.from_url(cls.url)
        cls.engine = create_engine(cls.url)

    @classmethod
    def tearDownClass(cls):
        cls.store.dispose()
        cls.engine.dispose()
        admin = create_engine(make_url(MYSQL_URL).set(database=None), isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{cls.database}`"))
        admin.dispose()

    def test_every_table_is_utf8mb4(self):
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME <> 'alembic_version'"
                )
            ).fetchall()

        self.assertEqual(len(rows), 8)
        for name, collation in rows:
            self.assertTrue(
                collation.startswith("utf8mb4"),
                f"表 {name} 的字符集是 {collation}，中文或 emoji 会存坏",
            )

    def test_every_text_column_is_utf8mb4(self):
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME FROM "
                    "information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                    "AND CHARACTER_SET_NAME IS NOT NULL"
                )
            ).fetchall()

        self.assertTrue(rows)
        for table, column, charset in rows:
            self.assertEqual(charset, "utf8mb4", f"{table}.{column} 的字符集是 {charset}")

    def test_chinese_and_emoji_survive_a_round_trip(self):
        rubric = from_dict(emoji_rubric_data())
        evaluation = evaluate(rubric, EMOJI_ANSWER, CannedProvider([emoji_payload()]), samples=1)

        stored = self.store.save_evaluation(
            rubric=rubric,
            answer_text=EMOJI_ANSWER,
            evaluation=evaluation,
            meta=RunMeta(
                model="deepseek-chat",
                params={"thinking": {"type": "enabled"}},
                tokens_in=1234,
                tokens_out=345,
                latency_ms=4200,
            ),
            student_pseudo_id="S-2024-001",
        )
        loaded = self.store.load_evaluation(answer_id=stored.answer_id)

        # 答卷（LONGTEXT）、评语与证据（LONGTEXT，附偏移）
        self.assertEqual(loaded.answer_text, EMOJI_ANSWER)
        self.assertEqual(loaded.student_pseudo_id, "S-2024-001")
        self.assertEqual(loaded.evaluation.samples[0].comment, EMOJI_COMMENT)
        self.assertEqual(loaded.evaluation.samples[0].items[2].evidence, EMOJI_EVIDENCE)
        self.assertTrue(loaded.evaluation.samples[0].items[2].grounded)

        # 标准快照（JSON 列）：题干与采分点描述里的 emoji 也必须原样读回
        self.assertEqual(loaded.rubric, rubric)
        self.assertIn("🚀", loaded.rubric.stem)
        self.assertIn("🎯", loaded.rubric.item("P1").desc)

        # 运行元信息与聚合值（DOUBLE / JSON）不能因为方言而变成别的类型
        run = loaded.runs[0]
        self.assertEqual(
            (run.model, run.tokens_in, run.tokens_out, run.latency_ms),
            ("deepseek-chat", 1234, 345, 4200),
        )
        self.assertEqual(loaded.evaluation.samples, evaluation.samples)
        self.assertEqual(loaded.evaluation.aggregate, evaluation.aggregate)
        self.assertEqual(loaded.evaluation.route, evaluation.route)


if __name__ == "__main__":
    unittest.main()
