"""存储层测试：一次评分的写入与读回。

接缝是 `Store` 的公开方法（保存 / 读取），下面是真实数据库：
默认用内存 SQLite 跑得快，MySQL 的字符集与方言差异由 `test_store_mysql.py` 单独覆盖。
"""

import copy
import unittest

try:
    import sqlalchemy  # noqa: F401
except ImportError:  # pragma: no cover - 仅在没装依赖的裸环境下触发
    raise unittest.SkipTest(
        "未安装 sqlalchemy，先执行：python3 -m venv .venv && .venv/bin/pip install -e backend"
    )

from app.core.normalize import content_hash, normalize_with_map
from app.core.rubric import from_dict
from app.pipeline import evaluate
from app.providers.fake import CannedProvider
from app.store import QuestionNotFound, RunMeta, Store
from tests.fixtures import ANSWER, RUBRIC_DATA, payload_text, payload_with_levels
from tests.support import create_schema


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.store = Store.from_url("sqlite+pysqlite:///:memory:")
        create_schema(self.store)
        self.addCleanup(self.store.dispose)
        self.rubric = from_dict(RUBRIC_DATA)

    def save(
        self,
        *,
        samples=1,
        responses=None,
        meta=None,
        rubric=None,
        answer_text=ANSWER,
        pseudo_id="anon",
        truncated=False,
    ):
        rubric = rubric or self.rubric
        provider = CannedProvider(responses or [payload_text()])
        evaluation = evaluate(rubric, answer_text, provider, samples=samples)
        stored = self.store.save_evaluation(
            rubric=rubric,
            answer_text=answer_text,
            evaluation=evaluation,
            meta=meta or RunMeta(model="deepseek-chat", prompt_version="v1"),
            student_pseudo_id=pseudo_id,
            truncated=truncated,
        )
        return stored, evaluation


class SaveOneEvaluation(StoreTestCase):
    def test_reads_back_every_field_by_answer_id(self):
        stored, evaluation = self.save()

        loaded = self.store.load_evaluation(answer_id=stored.answer_id)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.answer_text, ANSWER)
        self.assertEqual(loaded.rubric, self.rubric)
        self.assertEqual(loaded.run_ids, stored.run_ids)
        self.assertEqual(loaded.evaluation.samples, evaluation.samples)
        self.assertEqual(loaded.evaluation.aggregate, evaluation.aggregate)
        self.assertEqual(loaded.evaluation.route, evaluation.route)


class SaveMultiSampleEvaluation(StoreTestCase):
    def test_keeps_one_run_per_sample_and_reads_them_back_in_order(self):
        responses = [
            payload_with_levels(1.0, 0.5, 1.0),
            payload_with_levels(0.5, 0.5, 1.0),
            payload_with_levels(0.5, 0.5, 1.0),
        ]
        stored, evaluation = self.save(samples=3, responses=responses)

        loaded = self.store.load_evaluation(answer_id=stored.answer_id)

        self.assertEqual(len(loaded.run_ids), 3)
        self.assertEqual(
            [sample.levels() for sample in loaded.evaluation.samples],
            [sample.levels() for sample in evaluation.samples],
        )
        # P1 在 1.0 / 0.5 之间漂移，极差 1.5 已经超过阈值 → 必须转人工
        self.assertEqual(loaded.evaluation.aggregate.levels["P1"], 0.5)
        self.assertEqual(loaded.evaluation.aggregate.total_range, 1.5)
        self.assertEqual(loaded.evaluation.route.route, "human")
        self.assertTrue(loaded.evaluation.route.reasons)

    def test_reads_back_the_same_evaluation_by_run_id(self):
        stored, _ = self.save(samples=1)

        loaded = self.store.load_evaluation(run_id=stored.run_ids[0])

        self.assertEqual(loaded.answer_id, stored.answer_id)
        self.assertEqual(loaded.aggregate_id, stored.aggregate_id)


class RubricSnapshot(StoreTestCase):
    def test_unchanged_standard_reuses_the_same_snapshot(self):
        first, _ = self.save()
        second, _ = self.save()

        self.assertEqual(first.rubric_id, second.rubric_id)

    def test_old_run_still_shows_the_standard_it_was_graded_with(self):
        stored, _ = self.save()

        changed_data = copy.deepcopy(RUBRIC_DATA)
        changed_data["items"][1]["desc"] = "服务器回复 SYN+ACK（课后修订版）"
        changed = from_dict(changed_data)
        second, _ = self.save(rubric=changed)

        self.assertNotEqual(second.rubric_id, stored.rubric_id)
        self.assertEqual(self.store.load_evaluation(answer_id=stored.answer_id).rubric, self.rubric)
        self.assertEqual(self.store.load_evaluation(answer_id=second.answer_id).rubric, changed)


class KeepRunAndAnswerMetadata(StoreTestCase):
    def test_run_metadata_survives_the_round_trip(self):
        meta = RunMeta(
            model="deepseek-chat",
            prompt_version="v2",
            params={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            tokens_in=1200,
            tokens_out=360,
            latency_ms=4200,
        )
        stored, _ = self.save(meta=meta)

        loaded = self.store.load_evaluation(answer_id=stored.answer_id)

        self.assertEqual(len(loaded.runs), 1)
        run = loaded.runs[0]
        self.assertEqual(run.run_id, stored.run_ids[0])
        self.assertEqual(run.rubric_id, stored.rubric_id)
        self.assertEqual(run.model, "deepseek-chat")
        self.assertEqual(run.prompt_version, "v2")
        self.assertEqual(run.params, {"thinking": {"type": "enabled"}, "reasoning_effort": "high"})
        self.assertEqual((run.tokens_in, run.tokens_out, run.latency_ms), (1200, 360, 4200))
        self.assertEqual(run.status, "ok")
        self.assertIsNone(run.error)

    def test_answer_metadata_survives_the_round_trip(self):
        answer_text = "客户端发送 SYN 后 😀 空格与全角（Ａ）都要原样存回来"
        stored, _ = self.save(answer_text=answer_text, pseudo_id="S-2024-001", truncated=True)

        loaded = self.store.load_evaluation(answer_id=stored.answer_id)

        self.assertEqual(loaded.answer_text, answer_text)
        self.assertEqual(loaded.student_pseudo_id, "S-2024-001")
        self.assertTrue(loaded.truncated)
        self.assertEqual(loaded.normalized_text, normalize_with_map(answer_text)[0])


class RegradeKeepsRunsApart(StoreTestCase):
    def test_reading_by_run_id_returns_the_aggregate_that_contains_it(self):
        first, _ = self.save()
        second, _ = self.save()

        loaded = self.store.load_evaluation(run_id=first.run_ids[0])

        self.assertEqual(loaded.aggregate_id, first.aggregate_id)
        self.assertEqual(loaded.run_ids, first.run_ids)
        self.assertNotEqual(loaded.aggregate_id, second.aggregate_id)


class UnknownId(StoreTestCase):
    def test_missing_ids_return_none(self):
        self.assertIsNone(self.store.load_evaluation(answer_id=999))
        self.assertIsNone(self.store.load_evaluation(run_id=999))


class ImportAnswers(StoreTestCase):
    def setUp(self):
        super().setUp()
        # 先存一份标准，题目才会存在
        self.save()

    def test_imports_rows_and_assigns_pseudo_ids_in_order(self):
        outcome = self.store.import_answers(
            [("Q-01", "第一份"), ("Q-01", "第二份")], source="csv"
        )

        self.assertEqual(outcome.skipped_duplicates, 0)
        self.assertEqual([item.pseudo_id for item in outcome.imported], ["S-0001", "S-0002"])
        self.assertEqual(self.store.count_answers("Q-01"), 3)   # 1 份来自 save()

    def test_duplicates_are_skipped_by_normalised_content(self):
        self.store.import_answers([("Q-01", "客户端发送 SYN")], source="csv")

        outcome = self.store.import_answers(
            [("Q-01", "  客户端发送  SYN  ")], source="csv"
        )

        self.assertEqual(outcome.imported, [])
        self.assertEqual(outcome.skipped_duplicates, 1)

    def test_duplicates_inside_one_batch_are_skipped_too(self):
        outcome = self.store.import_answers(
            [("Q-01", "同一份"), ("Q-01", "同一份")], source="csv"
        )

        self.assertEqual(len(outcome.imported), 1)
        self.assertEqual(outcome.skipped_duplicates, 1)

    def test_unknown_question_is_rejected_and_nothing_is_written(self):
        with self.assertRaises(QuestionNotFound) as ctx:
            self.store.import_answers(
                [("Q-01", "好的一行"), ("Q-99", "题号不存在")], source="csv"
            )
        self.assertIn("Q-99", str(ctx.exception))
        self.assertEqual(self.store.count_answers("Q-01"), 1)   # 只留下 setUp 那份

    def test_find_duplicates_matches_by_question_and_content(self):
        self.store.import_answers([("Q-01", "重复的答案")], source="csv")
        digest = content_hash("重复的答案")

        found = self.store.find_duplicates([("Q-01", digest), ("Q-02", digest)])

        self.assertEqual(found, {("Q-01", digest)})

    def test_empty_batch_writes_nothing(self):
        outcome = self.store.import_answers([], source="csv")

        self.assertEqual(outcome.imported, [])
        self.assertEqual(self.store.count_answers("Q-01"), 1)

    def test_get_question_returns_the_standard_it_was_created_with(self):
        question = self.store.get_question("Q-01")

        self.assertEqual(question["code"], "Q-01")
        self.assertEqual(question["total_score"], 10)
        self.assertIsNone(self.store.get_question("Q-99"))

    def test_list_answers_reports_pseudo_id_and_source(self):
        self.store.import_answers([("Q-01", "导入的答卷")], source="csv")

        rows = self.store.list_answers("Q-01")

        self.assertEqual([row["pseudo_id"] for row in rows], ["anon", "S-0001"])
        self.assertEqual(rows[1]["source"], "csv")
        self.assertGreater(rows[1]["length"], 0)


if __name__ == "__main__":
    unittest.main()
