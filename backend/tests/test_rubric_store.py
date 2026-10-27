"""标准保存与迭代：存储层。

接缝是 `Store` 的公开方法（保存 / 列表 / 打开），下面是真实数据库（内存 SQLite）。
"模型能不能只改指出的那一处"由提示词测试守；这里守的是**保存之后账对不对**：
版本只增不改、题干与满分跟着走、任务快照不被后续编辑移动。
"""

import unittest

try:
    import sqlalchemy  # noqa: F401
except ImportError:  # pragma: no cover - 仅在没装依赖的裸环境下触发
    raise unittest.SkipTest(
        "未安装 sqlalchemy，先执行：python3 -m venv .venv && .venv/bin/pip install -e backend"
    )

from app.core.rubric import from_dict, to_dict
from app.pipeline import evaluate
from app.providers.fake import CannedProvider
from app.store import RubricNotFound, Store
from tests.fixtures import RUBRIC_DATA, payload_text, rubric_variant
from tests.support import create_schema


class RubricStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.store = Store.from_url("sqlite+pysqlite:///:memory:")
        create_schema(self.store)
        self.addCleanup(self.store.dispose)
        self.rubric = from_dict(RUBRIC_DATA)

    def save(self, rubric=None, *, source_text="教师口述的标准", feedback=""):
        return self.store.save_rubric(
            rubric or self.rubric, source_text=source_text, feedback=feedback
        )


class SaveAndReopen(RubricStoreTestCase):
    def test_saving_creates_the_question_and_the_first_version(self):
        saved = self.save()

        self.assertEqual(saved.question_code, "Q-01")
        self.assertEqual(saved.version, 1)
        self.assertFalse(saved.reused)

        record = self.store.get_rubric(saved.rubric_id)
        self.assertEqual(record["version"], 1)
        self.assertEqual(record["question_code"], "Q-01")
        self.assertEqual(record["source_text"], "教师口述的标准")
        self.assertEqual(record["feedback"], "")
        self.assertEqual(record["rubric"], to_dict(self.rubric))

    def test_saving_the_same_content_again_reuses_the_latest_version(self):
        first = self.save()
        second = self.save(feedback="内容其实没改")

        self.assertTrue(second.reused)
        self.assertEqual(second.rubric_id, first.rubric_id)
        self.assertEqual(second.version, 1)
        self.assertEqual(len(self.store.list_rubrics()), 1)

    def test_editing_one_row_appends_a_version_and_keeps_the_old_one(self):
        first = self.save()
        edited = from_dict(rubric_variant(_items={"P1": {"desc": "客户端发送 SYN，并给出初始序列号"}}))

        second = self.save(edited, feedback="P1 的描述写具体一点")

        self.assertFalse(second.reused)
        self.assertEqual(second.version, 2)
        self.assertEqual(second.rubric_id != first.rubric_id, True)
        self.assertEqual(
            self.store.get_rubric(first.rubric_id)["rubric"]["items"][0]["desc"],
            "客户端发送 SYN 及初始序列号",
        )
        self.assertEqual(
            self.store.get_rubric(second.rubric_id)["rubric"]["items"][0]["desc"],
            "客户端发送 SYN，并给出初始序列号",
        )
        self.assertEqual(self.store.get_rubric(second.rubric_id)["feedback"], "P1 的描述写具体一点")

    def test_unknown_rubric_is_reported_as_missing(self):
        self.assertIsNone(self.store.get_rubric(9999))

    def test_editing_records_the_parent_version(self):
        first = self.save()
        second = self.store.save_rubric(
            from_dict(rubric_variant(_items={"P1": {"desc": "改过的描述"}})),
            source_text="教师口述的标准",
            feedback="P1 改具体",
            based_on_rubric_id=first.rubric_id,
        )

        self.assertEqual(second.parent_rubric_id, first.rubric_id)
        self.assertEqual(
            self.store.get_rubric(second.rubric_id)["parent_rubric_id"], first.rubric_id
        )

    def test_saving_identical_content_fills_missing_provenance(self):
        # 评分写入路径可能先建过同一内容的标准，但没带教师原话
        evaluation = evaluate(
            self.rubric, "一份答卷", CannedProvider([payload_text()]), samples=1
        )
        stored = self.store.save_evaluation(
            rubric=self.rubric, answer_text="一份答卷", evaluation=evaluation
        )
        self.assertEqual(self.store.get_rubric(stored.rubric_id)["source_text"], "")

        saved = self.save(source_text="三个要点……")

        self.assertTrue(saved.reused)
        self.assertEqual(
            self.store.get_rubric(saved.rubric_id)["source_text"], "三个要点……"
        )

    def test_a_missing_baseline_is_rejected(self):
        with self.assertRaises(RubricNotFound):
            self.store.save_rubric(self.rubric, based_on_rubric_id=9999)


class Listing(RubricStoreTestCase):
    def test_list_shows_only_the_latest_version_of_each_question(self):
        self.save()
        self.save(from_dict(rubric_variant(_items={"P1": {"desc": "改过一次"}})))
        self.save(from_dict(rubric_variant(question_id="Q-02")))

        rows = self.store.list_rubrics()

        self.assertEqual([row["question_code"] for row in rows], ["Q-01", "Q-02"])
        q01 = rows[0]
        self.assertEqual(q01["version"], 2)
        self.assertEqual(q01["item_count"], 3)
        self.assertEqual(q01["total_score"], 10)
        self.assertIn("三次握手", q01["stem"])

    def test_saving_updates_stem_and_total_score_on_the_question(self):
        self.save()
        bigger = from_dict(
            rubric_variant(
                total_score=12,
                stem="简述 TCP 三次握手，并说明每次报文的作用。",
                _items={"P1": {"weight": 4}, "P2": {"weight": 4}, "P3": {"weight": 4}},
            )
        )

        self.save(bigger)

        question = self.store.get_question("Q-01")
        self.assertEqual(question["total_score"], 12)
        self.assertIn("每次报文的作用", question["stem"])


class TaskSnapshot(RubricStoreTestCase):
    def test_editing_a_rubric_does_not_move_a_task_created_before_it(self):
        first = self.save()
        outcome = self.store.import_answers([("Q-01", "一份答卷")], source="csv")
        task = self.store.create_task(
            question_code="Q-01",
            answer_ids=[outcome.imported[0].answer_id],
            samples=1,
        )
        self.assertEqual(task.rubric_id, first.rubric_id)

        edited = from_dict(rubric_variant(_items={"P1": {"desc": "后来改的描述"}}))
        second = self.save(edited)

        self.assertGreater(second.version, first.version)
        self.assertEqual(task.rubric_id, first.rubric_id)
        self.assertEqual(
            self.store.load_rubric(task.rubric_id).items[0].desc,
            "客户端发送 SYN 及初始序列号",
        )
        newest = self.store.create_task(
            question_code="Q-01",
            answer_ids=[outcome.imported[0].answer_id],
            samples=1,
        )
        self.assertEqual(newest.rubric_id, second.rubric_id)


if __name__ == "__main__":
    unittest.main()
