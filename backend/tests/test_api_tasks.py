"""批量评分任务接口的验收：建任务、查进度、单份重试、快照固定。

用真 SQLite 文件库 + 假 provider：任务跑在线程里，内存库只有一条连接会互相踩。
"""

import tempfile
import time
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("未安装 fastapi/httpx，见 backend/pyproject.toml")

from app.auth import AdminAuth
from app.core.rubric import from_dict
from app.main import create_app
from app.pipeline import evaluate
from app.providers.fake import CannedProvider
from app.store import Store
from tests.fixtures import ANSWER, RUBRIC_DATA, payload_text
from tests.support import create_schema, database_url

PASSWORD = "let-me-in-please"


class FailingProvider(CannedProvider):
    """按提示词内容决定失败，用来验证"单份失败不影响整批"。"""

    def complete(self, messages, *, sample_index: int = 0) -> str:
        if "会失败" in messages[-1]["content"]:
            raise RuntimeError("模拟模型故障")
        return super().complete(messages, sample_index=sample_index)


class TaskApiTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.url = f"sqlite:///{Path(self.dir.name) / 'tasks-api.db'}"
        self.store = Store.from_url(self.url)
        create_schema(self.store)
        self.addCleanup(self.store.dispose)

        self._save_rubric()

        # 先把 DATABASE_URL 指过去，再装配应用——create_app 在构造时就建 Store
        env = database_url(self.url)
        env.__enter__()
        self.addCleanup(lambda: env.__exit__(None, None, None))
        self.app = create_app(
            auth=AdminAuth(PASSWORD, ttl_seconds=600),
            provider_factory=lambda: FailingProvider([payload_text()]),
        )

        # 用 with 进 lifespan：startup 的 mark_interrupted_tasks 只有这样才能被跑到
        self._client_cm = TestClient(self.app)
        self.client = self._client_cm.__enter__()
        self.addCleanup(lambda: self._client_cm.__exit__(None, None, None))
        self.client.post("/api/auth/login", json={"password": PASSWORD})

    def tearDown(self):
        # 任务跑在线程里：先等它停，再让临时库被删掉，否则会看到
        # "disk I/O error" 这种与断言无关的噪音
        runner = getattr(self.app.state, "runner", None)
        if runner is not None:
            runner.wait_all(timeout=15)

    def _save_rubric(self):
        rubric = from_dict(RUBRIC_DATA)
        evaluation = evaluate(rubric, ANSWER, CannedProvider([payload_text()]), samples=1)
        self.store.save_evaluation(rubric=rubric, answer_text=ANSWER, evaluation=evaluation)

    def import_answers(self, texts):
        outcome = self.store.import_answers(
            [("Q-01", text) for text in texts], source="csv", skip_duplicates=False
        )
        return [item.answer_id for item in outcome.imported]

    def wait_for(self, task_id, expected_total=1, attempts=200):
        for _ in range(attempts):
            body = self.client.get(f"/api/tasks/{task_id}").json()
            if body["status"] != "running" and body["done"] + body["failed"] >= expected_total:
                return body
            time.sleep(0.05)
        self.fail(f"任务 {task_id} 没跑完：{body}")


class CreateAndPoll(TaskApiTestCase):
    def test_creating_a_task_returns_an_id_and_then_progress(self):
        answer_ids = self.import_answers([ANSWER])

        created = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": answer_ids, "samples": 1},
        )

        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertIsInstance(body["task_id"], int)
        self.assertEqual(body["total"], 1)

        final = self.wait_for(body["task_id"])
        self.assertEqual(final["status"], "done")
        self.assertEqual(final["done"], 1)
        self.assertEqual(final["items"][0]["status"], "graded")
        # 教师看到的是三态之一：已评 / 待复核 / 失败
        self.assertEqual(final["items"][0]["state"], "graded")
        self.assertEqual(final["items"][0]["route"], "auto")
        self.assertIsNotNone(final["items"][0]["score"])

    def test_finalized_result_shows_up_as_finalized_on_the_task(self):
        answer_ids = self.import_answers([ANSWER])
        created = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": answer_ids, "samples": 1},
        ).json()
        item = self.wait_for(created["task_id"])["items"][0]

        accepted = self.client.post(
            "/api/results/batch-accept", json={"aggregate_ids": [item["aggregate_id"]]}
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)

        refreshed = self.client.get(f"/api/tasks/{created['task_id']}").json()["items"][0]
        self.assertEqual(refreshed["state"], "finalized")
        self.assertEqual(refreshed["final_score"], item["score"])
        self.assertEqual(refreshed["score"], item["score"])  # 模型原分仍然保留

    def test_regraded_answer_leaves_the_old_task_item_marked_superseded(self):
        answer_ids = self.import_answers([ANSWER])
        first = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": answer_ids, "samples": 1},
        ).json()
        self.wait_for(first["task_id"])
        second = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": answer_ids, "samples": 1},
        ).json()
        self.wait_for(second["task_id"])

        current_item = self.client.get(f"/api/tasks/{second['task_id']}").json()["items"][0]
        self.assertTrue(current_item["current"])
        accepted = self.client.post(
            "/api/results/batch-accept",
            json={"aggregate_ids": [current_item["aggregate_id"]]},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)

        stale_item = self.client.get(f"/api/tasks/{first['task_id']}").json()["items"][0]
        self.assertEqual(stale_item["state"], "superseded")
        self.assertFalse(stale_item["current"])
        self.assertIsNone(stale_item["final_score"])

    def test_unknown_task_is_404(self):
        self.assertEqual(self.client.get("/api/tasks/9999").status_code, 404)

    def test_empty_selection_is_rejected_by_validation(self):
        response = self.client.post(
            "/api/tasks", json={"question_code": "Q-01", "answer_ids": [], "samples": 1}
        )
        self.assertEqual(response.status_code, 422)

    def test_duplicate_answer_ids_are_deduped(self):
        answer_ids = self.import_answers([ANSWER])

        body = self.client.post(
            "/api/tasks",
            json={
                "question_code": "Q-01",
                "answer_ids": [answer_ids[0], answer_ids[0]],
                "samples": 1,
            },
        )

        self.assertEqual(body.status_code, 200, body.text)
        self.assertEqual(body.json()["total"], 1)

    def test_answer_from_another_question_is_a_400(self):
        response = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": [9999], "samples": 1},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("9999", response.json()["detail"])

    def test_samples_are_bounded(self):
        answer_ids = self.import_answers([ANSWER])
        response = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": answer_ids, "samples": 99},
        )
        self.assertEqual(response.status_code, 422)


class InterruptedTaskCanResume(TaskApiTestCase):
    def test_retry_restarts_an_interrupted_task_that_has_only_pending_items(self):
        """执行器在领取第一份之前就挂了：failed=0，但仍然有活要干。"""
        answer_ids = self.import_answers([ANSWER])
        task = self.store.create_task(
            question_code="Q-01", answer_ids=answer_ids, samples=1
        )
        self.store.set_task_status(task.task_id, "interrupted", error="进程重启，任务中断")

        retried = self.client.post(
            f"/api/tasks/{task.task_id}/retry", json={"answer_ids": []}
        )

        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["reset"], 0)
        final = self.wait_for(task.task_id)
        self.assertEqual(final["status"], "done")
        self.assertEqual(final["items"][0]["state"], "graded")

    def test_retry_starts_a_task_that_never_started(self):
        """任务状态还是 pending、没有一个失败项：重试也必须真的把执行器拉起来。"""
        answer_ids = self.import_answers([ANSWER])
        task = self.store.create_task(
            question_code="Q-01", answer_ids=answer_ids, samples=1
        )
        self.assertEqual(self.store.get_task(task.task_id).status, "pending")

        retried = self.client.post(
            f"/api/tasks/{task.task_id}/retry", json={"answer_ids": []}
        )

        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["reset"], 0)
        final = self.wait_for(task.task_id)
        self.assertEqual(final["status"], "done")


class OneFailureDoesNotStopTheBatch(TaskApiTestCase):
    def test_failed_item_is_isolated_and_reports_a_reason(self):
        answer_ids = self.import_answers([ANSWER, "会失败的答卷", ANSWER + " 补充一句"])

        created = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": answer_ids, "samples": 1},
        ).json()
        final = self.wait_for(created["task_id"], expected_total=3)

        self.assertEqual((final["done"], final["failed"]), (2, 1))
        failed = [item for item in final["items"] if item["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("内部错误", failed[0]["error"])

    def test_retry_only_resets_the_failed_ones(self):
        answer_ids = self.import_answers([ANSWER, "会失败的答卷"])
        created = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": answer_ids, "samples": 1},
        ).json()
        self.wait_for(created["task_id"], expected_total=2)

        retried = self.client.post(
            f"/api/tasks/{created['task_id']}/retry",
            json={"answer_ids": []},
        )

        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["reset"], 1)

    def test_retrying_a_specific_answer_only_touches_that_one(self):
        answer_ids = self.import_answers(["会失败的一", "会失败的二"])
        created = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": answer_ids, "samples": 1},
        ).json()
        self.wait_for(created["task_id"], expected_total=2)

        retried = self.client.post(
            f"/api/tasks/{created['task_id']}/retry",
            json={"answer_ids": [answer_ids[0]]},
        )

        self.assertEqual(retried.json()["reset"], 1)


class SnapshotAndAuth(TaskApiTestCase):
    def test_task_uses_the_rubric_version_from_creation_time(self):
        answer_ids = self.import_answers([ANSWER])
        created = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": answer_ids, "samples": 1},
        ).json()
        final = self.wait_for(created["task_id"])

        loaded = self.store.load_evaluation(answer_id=answer_ids[0])
        self.assertEqual(loaded.rubric, from_dict(RUBRIC_DATA))
        # 聚合结果必须指向任务快照那一行，而不是"按内容又插出来的一版"
        self.assertEqual(loaded.rubric_id, created["rubric_id"])
        self.assertEqual(final["items"][0]["max_score"], 10)

    def test_task_routes_require_login(self):
        client = TestClient(self.app)
        self.assertEqual(client.post("/api/tasks", json={}).status_code, 401)
        self.assertEqual(client.get("/api/tasks/1").status_code, 401)


if __name__ == "__main__":
    unittest.main()
