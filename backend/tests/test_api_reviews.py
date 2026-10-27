"""复核接口的验收：队列、详情、改分、批量通过、定稿后不回改。

用真 SQLite 文件库 + 假 provider + 登录后的 TestClient，
从 HTTP 这条缝断言教师真正能看到、能提交的东西。
"""

import tempfile
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
from tests.fixtures import (
    ANSWER,
    MILD_DISAGREEMENT,
    RUBRIC_DATA,
    STRONG_DISAGREEMENT,
    payload_text,
)
from tests.support import create_schema, database_url

PASSWORD = "let-me-in-please"


class ReviewApiTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.url = f"sqlite:///{Path(self.dir.name) / 'reviews-api.db'}"
        self.store = Store.from_url(self.url)
        create_schema(self.store)
        self.addCleanup(self.store.dispose)
        self.rubric = from_dict(RUBRIC_DATA)

        env = database_url(self.url)
        env.__enter__()
        self.addCleanup(lambda: env.__exit__(None, None, None))
        self.app = create_app(
            auth=AdminAuth(PASSWORD, ttl_seconds=600),
            provider_factory=lambda: CannedProvider([payload_text()]),
        )

        self._client_cm = TestClient(self.app)
        self.client = self._client_cm.__enter__()
        self.addCleanup(lambda: self._client_cm.__exit__(None, None, None))
        self.client.post("/api/auth/login", json={"password": PASSWORD})

    def save(self, *, responses=None, samples=1, rubric=None, answer_text=ANSWER):
        rubric = rubric or self.rubric
        evaluation = evaluate(
            rubric, answer_text, CannedProvider(responses or [payload_text()]), samples=samples
        )
        return self.store.save_evaluation(
            rubric=rubric, answer_text=answer_text, evaluation=evaluation
        )

    def save_high_confidence(self):
        stored = self.save()
        self.assertEqual(stored.evaluation.route.route, "auto")
        return stored

    def save_needs_review(self, responses=MILD_DISAGREEMENT):
        stored = self.save(responses=responses, samples=len(responses))
        self.assertEqual(stored.evaluation.route.route, "human")
        return stored

    def review(self, aggregate_id, **payload):
        return self.client.post(f"/api/results/{aggregate_id}/review", json=payload)

    def batch_accept(self, aggregate_ids):
        return self.client.post(
            "/api/results/batch-accept", json={"aggregate_ids": aggregate_ids}
        )


class Queue(ReviewApiTestCase):
    def test_queue_requires_login(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/results").status_code, 401)

    def test_queue_lists_only_results_that_need_review_riskiest_first(self):
        auto = self.save_high_confidence()
        mild = self.save_needs_review(MILD_DISAGREEMENT)
        strong = self.save_needs_review(STRONG_DISAGREEMENT)

        body = self.client.get("/api/results").json()

        self.assertEqual(
            [row["aggregate_id"] for row in body["items"]],
            [strong.aggregate_id, mild.aggregate_id],
        )
        self.assertGreater(body["items"][0]["risk"], body["items"][1]["risk"])
        self.assertNotIn(auto.aggregate_id, [row["aggregate_id"] for row in body["items"]])
        self.assertFalse(body["items"][0]["reviewed"])
        self.assertEqual(body["total"], 2)

    def test_queue_can_list_high_confidence_results_waiting_for_confirmation(self):
        auto = self.save_high_confidence()
        self.save_needs_review()

        body = self.client.get("/api/results", params={"route": "auto"}).json()

        self.assertEqual([row["aggregate_id"] for row in body["items"]], [auto.aggregate_id])

    def test_queue_rejects_an_unknown_route(self):
        response = self.client.get("/api/results", params={"route": "maybe"})
        self.assertEqual(response.status_code, 422)

    def test_queue_page_can_be_offset(self):
        self.save_high_confidence()
        mild = self.save_needs_review(MILD_DISAGREEMENT)
        strong = self.save_needs_review(STRONG_DISAGREEMENT)

        first = self.client.get("/api/results", params={"limit": 1}).json()
        second = self.client.get("/api/results", params={"limit": 1, "offset": 1}).json()

        self.assertEqual(first["items"][0]["aggregate_id"], strong.aggregate_id)
        self.assertEqual(second["items"][0]["aggregate_id"], mild.aggregate_id)
        self.assertEqual(second["total"], 2)

    def test_a_finalized_answer_cannot_be_regraded_via_a_new_task(self):
        stored = self.save_high_confidence()
        self.review(stored.aggregate_id, decision="accept")

        response = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": [stored.answer_id], "samples": 1},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("定稿", response.json()["detail"])

    def test_queue_can_show_finalized_results(self):
        stored = self.save_needs_review()
        self.review(stored.aggregate_id, decision="override", final_score=6, comment="扣两分")

        body = self.client.get("/api/results", params={"pending": "false"}).json()

        self.assertEqual([row["aggregate_id"] for row in body["items"]], [stored.aggregate_id])
        self.assertTrue(body["items"][0]["reviewed"])
        self.assertEqual(body["items"][0]["final_score"], 6.0)


class Detail(ReviewApiTestCase):
    def test_detail_shows_evidence_spans_sample_levels_and_disagreement(self):
        stored = self.save_needs_review()

        response = self.client.get(f"/api/results/{stored.aggregate_id}")
        body = response.json()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(body["rubric"]["question_id"], "Q-01")
        self.assertEqual(body["answer"]["text"], ANSWER)
        self.assertEqual(body["score"]["raw"], 7.0)
        self.assertEqual(body["score"]["max"], 10)
        self.assertEqual(body["score"]["route"], "human")
        self.assertTrue(body["score"]["reasons"])
        self.assertIsNone(body["review"])
        self.assertEqual([sample["score"] for sample in body["samples"]], [10.0, 7.0])

        items = {item["id"]: item for item in body["items"]}
        first = items["P1"]
        self.assertEqual(first["sample_levels"], [1.0, 0.0])
        self.assertTrue(first["disagree"])
        self.assertIsNotNone(first["span"])
        start, end = first["span"]["start"], first["span"]["end"]
        self.assertEqual(ANSWER[start:end], first["evidence"])
        self.assertTrue(first["levels"])  # 当时的等级与判定依据也在响应里

    def test_detail_reports_a_missing_result_as_404(self):
        self.assertEqual(self.client.get("/api/results/9999").status_code, 404)

    def test_detail_marks_a_superseded_result_as_not_current(self):
        previous = self.save_needs_review()
        evaluation = evaluate(self.rubric, ANSWER, CannedProvider([payload_text()]), samples=1)
        current = self.store.save_evaluation_for_answer(
            answer_id=previous.answer_id, rubric=self.rubric, evaluation=evaluation
        )

        stale = self.client.get(f"/api/results/{previous.aggregate_id}").json()
        fresh = self.client.get(f"/api/results/{current.aggregate_id}").json()

        self.assertFalse(stale["current"])
        self.assertEqual(stale["superseded_by"], current.aggregate_id)
        self.assertTrue(fresh["current"])
        self.assertIsNone(fresh["superseded_by"])

    def test_detail_requires_login(self):
        stored = self.save_needs_review()
        self.client.cookies.clear()
        self.assertEqual(
            self.client.get(f"/api/results/{stored.aggregate_id}").status_code, 401
        )


class Reviewing(ReviewApiTestCase):
    def test_accept_finalizes_with_the_model_score(self):
        stored = self.save_needs_review()

        response = self.review(stored.aggregate_id, decision="accept", comment="逐条看过")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["decision"], "accept")
        self.assertEqual(body["final_score"], stored.evaluation.aggregate.score)
        self.assertEqual(body["reviewer_id"], "admin")

        detail = self.client.get(f"/api/results/{stored.aggregate_id}").json()
        self.assertEqual(detail["review"]["comment"], "逐条看过")
        self.assertEqual(detail["score"]["final"], stored.evaluation.aggregate.score)
        # 原分保留，不被改写
        self.assertEqual(detail["score"]["raw"], stored.evaluation.aggregate.score)

    def test_override_records_the_new_score_and_removes_it_from_the_queue(self):
        stored = self.save_needs_review()

        response = self.review(
            stored.aggregate_id,
            decision="override",
            final_score=5.5,
            comment="P2 的证据只覆盖半个要点",
        )

        self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get(f"/api/results/{stored.aggregate_id}").json()
        self.assertEqual(detail["score"]["raw"], 7.0)
        self.assertEqual(detail["score"]["final"], 5.5)
        self.assertEqual(detail["review"]["decision"], "override")
        self.assertEqual(detail["review"]["comment"], "P2 的证据只覆盖半个要点")
        self.assertEqual(self.client.get("/api/results").json()["items"], [])

    def test_override_without_a_comment_is_rejected(self):
        stored = self.save_needs_review()

        response = self.review(
            stored.aggregate_id, decision="override", final_score=5.5
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "REVIEW_REJECTED")

    def test_override_with_a_non_finite_score_is_rejected(self):
        stored = self.save_needs_review()

        response = self.client.post(
            f"/api/results/{stored.aggregate_id}/review",
            content='{"decision":"override","final_score":NaN,"comment":"胡说"}',
            headers={"content-type": "application/json"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "REVIEW_REJECTED")
        self.assertIsNone(
            self.client.get(f"/api/results/{stored.aggregate_id}").json()["review"]
        )

    def test_override_outside_the_score_range_is_rejected(self):
        stored = self.save_needs_review()

        response = self.review(
            stored.aggregate_id, decision="override", final_score=11, comment="越界"
        )

        self.assertEqual(response.status_code, 400)

    def test_second_review_is_a_conflict(self):
        stored = self.save_needs_review()
        self.review(stored.aggregate_id, decision="accept")

        response = self.review(stored.aggregate_id, decision="accept")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "RESULT_FINALIZED")

    def test_reviewing_a_superseded_result_is_a_conflict(self):
        previous = self.save_needs_review()
        evaluation = evaluate(self.rubric, ANSWER, CannedProvider([payload_text()]), samples=1)
        self.store.save_evaluation_for_answer(
            answer_id=previous.answer_id, rubric=self.rubric, evaluation=evaluation
        )

        response = self.review(previous.aggregate_id, decision="accept")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "RESULT_SUPERSEDED")

    def test_review_requires_login(self):
        stored = self.save_needs_review()
        self.client.cookies.clear()

        self.assertEqual(
            self.review(stored.aggregate_id, decision="accept").status_code, 401
        )


class BatchPass(ReviewApiTestCase):
    def test_batch_pass_finalizes_high_confidence_results(self):
        first = self.save_high_confidence()
        second = self.save_high_confidence()

        response = self.batch_accept([first.aggregate_id, second.aggregate_id])

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["reviews"]), 2)
        self.assertEqual(self.client.get("/api/results", params={"route": "auto"}).json()["items"], [])
        detail = self.client.get(f"/api/results/{first.aggregate_id}").json()
        self.assertIsNotNone(detail["review"])
        self.assertEqual(detail["score"]["final"], detail["score"]["raw"])

    def test_batch_pass_refuses_results_that_need_review(self):
        auto = self.save_high_confidence()
        human = self.save_needs_review()

        response = self.batch_accept([auto.aggregate_id, human.aggregate_id])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "REVIEW_REJECTED")
        detail = self.client.get(f"/api/results/{auto.aggregate_id}").json()
        self.assertIsNone(detail["review"])

    def test_batch_pass_rejects_unknown_ids(self):
        response = self.batch_accept([9999])
        self.assertEqual(response.status_code, 400)

    def test_batch_pass_rejects_an_empty_selection(self):
        response = self.batch_accept([])
        self.assertEqual(response.status_code, 422)

    def test_batch_pass_requires_login(self):
        stored = self.save_high_confidence()
        self.client.cookies.clear()

        self.assertEqual(self.batch_accept([stored.aggregate_id]).status_code, 401)


class FinalizedSnapshots(ReviewApiTestCase):
    def test_detail_still_shows_the_old_standard_after_the_rubric_changes(self):
        stored = self.save_high_confidence()
        self.review(stored.aggregate_id, decision="accept")

        changed = from_dict(RUBRIC_DATA)
        changed.items[0].desc = "改过的采分点描述"
        self.save(
            rubric=changed,
            answer_text="改标准之后录入的另一份答卷",
            responses=[payload_text()],
        )

        body = self.client.get(f"/api/results/{stored.aggregate_id}").json()

        self.assertEqual(body["items"][0]["desc"], "客户端发送 SYN 及初始序列号")
        self.assertEqual(body["score"]["final"], 8.0)


if __name__ == "__main__":
    unittest.main()
