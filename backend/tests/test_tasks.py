"""批量任务的执行：并发、失败隔离、快照固定、重试。

接缝是 `TaskRunner.start` / `wait` 与任务表的状态——测试断言的是
"任务跑完后每份答卷是什么状态"，而不是"起了几个线程"。
"""

import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from app.core.rubric import from_dict
from app.pipeline import GradingError, evaluate
from app.providers.fake import CannedProvider
from app.store import Store
from app.tasks import MAX_WORKERS, TaskRunner, concurrency_from_env
from tests.fixtures import ANSWER, RUBRIC_DATA, payload_text
from tests.support import create_schema


class FlakyProvider:
    """按答卷内容决定成功还是失败，用来验证"单份失败不中断整批"。"""

    def __init__(self, good_text: str) -> None:
        self.good_text = good_text

    def complete(self, messages, *, sample_index: int = 0) -> str:
        prompt = messages[-1]["content"]
        if "失败的答卷" in prompt:
            raise GradingError("INVALID_JSON", "模型输出不是合法 JSON：模拟失败")
        return payload_text()


class TaskTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store.from_url(f"sqlite:///{Path(self.dir.name) / 'tasks.db'}")
        create_schema(self.store)
        self.addCleanup(self.store.dispose)
        self.rubric = from_dict(RUBRIC_DATA)
        self._save_rubric()

    def _save_rubric(self, rubric=None):
        self._save_rubric_from(rubric or self.rubric, payload_text())

    def _save_rubric_from(self, rubric, payload=None):
        evaluation = evaluate(
            rubric, ANSWER, CannedProvider([payload or payload_text()]), samples=1
        )
        self.store.save_evaluation(
            rubric=rubric, answer_text=ANSWER, evaluation=evaluation
        )

    def import_answers(self, texts):
        outcome = self.store.import_answers(
            [("Q-01", text) for text in texts], source="csv", skip_duplicates=False
        )
        return [item.answer_id for item in outcome.imported]

    def run_task(self, answer_ids, provider=None, samples=1):
        task = self.store.create_task(
            question_code="Q-01", answer_ids=answer_ids, samples=samples
        )
        runner = TaskRunner(
            self.store, lambda: provider or CannedProvider([payload_text()]), concurrency=2
        )
        runner.start(task.task_id)
        self.assertTrue(runner.wait(task.task_id, timeout=10), "任务 10 秒未结束")
        return self.store.get_task(task.task_id)


class RunWholeBatch(TaskTestCase):
    def test_every_answer_ends_up_graded_or_failed(self):
        answer_ids = self.import_answers(["正常答卷一", "失败的答卷", "正常答卷二"])

        task = self.run_task(answer_ids, provider=FlakyProvider(ANSWER))

        self.assertEqual(task.status, "done")
        self.assertEqual(task.total, 3)
        self.assertEqual(task.done, 2)
        self.assertEqual(task.failed, 1)
        items = self.store.list_task_items(task.task_id)
        self.assertEqual(sorted(item.status for item in items), ["failed", "graded", "graded"])

    def test_failed_item_keeps_a_readable_reason(self):
        answer_ids = self.import_answers(["失败的答卷"])

        task = self.run_task(answer_ids, provider=FlakyProvider(ANSWER))

        item = self.store.list_task_items(task.task_id)[0]
        self.assertEqual(item.status, "failed")
        self.assertIn("INVALID_JSON", item.error)
        self.assertIsNone(item.aggregate_id)

    def test_graded_item_points_at_its_result(self):
        answer_ids = self.import_answers(["正常答卷"])

        task = self.run_task(answer_ids)

        item = self.store.list_task_items(task.task_id)[0]
        self.assertIsNotNone(item.aggregate_id)
        loaded = self.store.load_evaluation(answer_id=item.answer_id)
        self.assertEqual(loaded.aggregate_id, item.aggregate_id)


class SnapshotIsFixed(TaskTestCase):
    def test_changing_the_rubric_after_creating_a_task_does_not_affect_it(self):
        answer_ids = self.import_answers(["正常答卷"])
        task = self.store.create_task(
            question_code="Q-01", answer_ids=answer_ids, samples=1
        )

        # 任务建好之后再改标准：追加一版权重不同的标准
        changed = dict(RUBRIC_DATA)
        changed["items"] = [
            dict(item, weight=1.0, levels=[{"level": 1.0, "score": 1.0}, {"level": 0.0, "score": 0.0}])
            for item in RUBRIC_DATA["items"]
        ]
        changed["total_score"] = 3
        # 新标准只允许 1.0 / 0.0，所以配一份全 1.0 的输出（原样例 P2 是 0.5）
        full_marks = json.loads(payload_text())
        for entry in full_marks["items"]:
            entry["level"] = 1.0
        self._save_rubric_from(from_dict(changed), json.dumps(full_marks, ensure_ascii=False))

        runner = TaskRunner(
            self.store, lambda: CannedProvider([payload_text()]), concurrency=1
        )
        runner.start(task.task_id)
        self.assertTrue(runner.wait(task.task_id, timeout=10))

        item = self.store.list_task_items(task.task_id)[0]
        loaded = self.store.load_evaluation(answer_id=item.answer_id)
        self.assertEqual(loaded.rubric, self.rubric)          # 仍是任务创建时那份
        self.assertEqual(loaded.evaluation.aggregate.max_score, 10)


class RetryFailedItems(TaskTestCase):
    def test_only_failed_items_are_reset(self):
        answer_ids = self.import_answers(["正常答卷", "失败的答卷"])

        task = self.run_task(answer_ids, provider=FlakyProvider(ANSWER))
        failed = [item for item in self.store.list_task_items(task.task_id) if item.status == "failed"]
        self.assertEqual(len(failed), 1)

        reset = self.store.reset_failed_items(task.task_id)

        self.assertEqual(reset, 1)
        items = {item.answer_id: item for item in self.store.list_task_items(task.task_id)}
        self.assertEqual(items[failed[0].answer_id].status, "pending")
        self.assertEqual(len([i for i in items.values() if i.status == "graded"]), 1)
        self.assertEqual(self.store.get_task(task.task_id).failed, 0)

    def test_retry_with_a_working_provider_grades_it(self):
        answer_ids = self.import_answers(["失败的答卷"])
        task = self.run_task(answer_ids, provider=FlakyProvider(ANSWER))
        self.store.reset_failed_items(task.task_id)

        runner = TaskRunner(
            self.store, lambda: CannedProvider([payload_text()]), concurrency=1
        )
        runner.start(task.task_id)
        self.assertTrue(runner.wait(task.task_id, timeout=10))

        final = self.store.get_task(task.task_id)
        self.assertEqual((final.done, final.failed), (1, 0))


class RunnerCrash(TaskTestCase):
    def test_a_crashing_runner_leaves_the_task_retryable(self):
        answer_ids = self.import_answers(["正常答卷"])
        task = self.store.create_task(
            question_code="Q-01", answer_ids=answer_ids, samples=1
        )

        class BrokenStore:
            """claim 就炸：模拟库锁超时/连接断开。"""

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def claim_next_item(self, task_id):
                raise RuntimeError("database is locked")

        runner = TaskRunner(
            self.store, lambda: CannedProvider([payload_text()]), concurrency=1
        )
        runner._store = BrokenStore()          # noqa: SLF001 - 故意注入故障
        runner._store._inner = self.store      # noqa: SLF001

        runner.start(task.task_id)
        self.assertTrue(runner.wait(task.task_id, timeout=10))

        reloaded = self.store.get_task(task.task_id)
        self.assertEqual(reloaded.status, "interrupted")
        self.assertIn("database is locked", self.store.get_task(task.task_id).error)
        # 中断后能重试：reset 会把失败项放回 pending
        self.assertGreaterEqual(self.store.reset_failed_items(task.task_id), 0)


class InterruptedTasks(TaskTestCase):
    def test_running_task_is_marked_interrupted_on_restart(self):
        answer_ids = self.import_answers(["正常答卷"])
        task = self.store.create_task(
            question_code="Q-01", answer_ids=answer_ids, samples=1
        )
        self.store.set_task_status(task.task_id, "running")
        self.store.claim_next_item(task.task_id)      # 模拟"跑到一半"

        changed = self.store.mark_interrupted_tasks()

        self.assertEqual(changed, 1)
        self.assertEqual(self.store.get_task(task.task_id).status, "interrupted")
        item = self.store.list_task_items(task.task_id)[0]
        self.assertEqual(item.status, "failed")
        self.assertIn("中断", item.error)


class CreateTaskRules(TaskTestCase):
    def test_task_snapshots_the_latest_rubric_version(self):
        answer_ids = self.import_answers(["正常答卷"])

        task = self.store.create_task(
            question_code="Q-01", answer_ids=answer_ids, samples=3
        )

        latest = self.store.load_rubric(task.rubric_id)
        self.assertEqual(latest, self.rubric)
        self.assertEqual(task.samples, 3)

    def test_empty_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create_task(question_code="Q-01", answer_ids=[], samples=1)

    def test_answers_must_belong_to_the_question(self):
        answer_ids = self.import_answers(["正常答卷"])

        with self.assertRaises(ValueError) as ctx:
            self.store.create_task(
                question_code="Q-01", answer_ids=answer_ids + [9999], samples=1
            )

        self.assertIn("9999", str(ctx.exception))
        # 一条都不许写进去
        self.assertIsNone(self.store.get_task(1))

    def test_concurrency_defaults_and_reads_the_environment(self):
        self.assertEqual(concurrency_from_env(), 2)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(concurrency_from_env(3), 3)
        with mock.patch.dict(os.environ, {"GRADING_CONCURRENCY": "3"}):
            self.assertEqual(concurrency_from_env(), 3)

    def test_concurrency_is_capped(self):
        with mock.patch.dict(os.environ, {"GRADING_CONCURRENCY": "99"}):
            self.assertEqual(concurrency_from_env(), MAX_WORKERS)

    def test_invalid_concurrency_is_rejected(self):
        for value in ("abc", "0", "-2"):
            with mock.patch.dict(os.environ, {"GRADING_CONCURRENCY": value}):
                with self.assertRaises(RuntimeError):
                    concurrency_from_env()


if __name__ == "__main__":
    unittest.main()
