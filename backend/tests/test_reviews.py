"""复核与改分：队列、详情、定稿、批量通过。

接缝是 `Store` 的公开方法，下面是真实数据库（内存 SQLite）。
这里只测"结论与账"——队列里该有谁、定稿分是多少、复核记录记了谁；
证据高亮与采样分歧怎么拼成一份响应，由 `test_api_reviews.py` 从 HTTP 那条缝断言。
"""

import unittest

try:
    import sqlalchemy  # noqa: F401
except ImportError:  # pragma: no cover - 仅在没装依赖的裸环境下触发
    raise unittest.SkipTest(
        "未安装 sqlalchemy，先执行：python3 -m venv .venv && .venv/bin/pip install -e backend"
    )

from app.core.rubric import from_dict
from app.pipeline import evaluate
from app.providers.fake import CannedProvider
from app.store import (
    ResultFinalized,
    ResultNotFound,
    ResultSuperseded,
    ReviewRejected,
    Store,
)
from tests.fixtures import (
    ANSWER,
    MILD_DISAGREEMENT,
    RUBRIC_DATA,
    STRONG_DISAGREEMENT,
    payload_text,
)
from tests.support import create_schema


class ReviewStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.store = Store.from_url("sqlite+pysqlite:///:memory:")
        create_schema(self.store)
        self.addCleanup(self.store.dispose)
        self.rubric = from_dict(RUBRIC_DATA)

    def save(self, *, responses=None, samples=1, rubric=None, answer_text=ANSWER):
        rubric = rubric or self.rubric
        evaluation = evaluate(
            rubric, answer_text, CannedProvider(responses or [payload_text()]), samples=samples
        )
        return self.store.save_evaluation(
            rubric=rubric, answer_text=answer_text, evaluation=evaluation
        )

    def save_high_confidence(self, answer_text=ANSWER):
        stored = self.save(answer_text=answer_text)
        self.assertEqual(stored.evaluation.route.route, "auto")
        return stored

    def save_needs_review(self, responses, answer_text=ANSWER, rubric=None):
        stored = self.save(
            responses=responses,
            samples=len(responses),
            answer_text=answer_text,
            rubric=rubric,
        )
        self.assertEqual(stored.evaluation.route.route, "human")
        return stored


class Queue(ReviewStoreTestCase):
    def test_queue_lists_only_results_that_need_review_highest_risk_first(self):
        self.save_high_confidence()
        mild = self.save_needs_review(MILD_DISAGREEMENT)
        strong = self.save_needs_review(STRONG_DISAGREEMENT)

        rows = self.store.list_results()

        self.assertEqual(
            [row["aggregate_id"] for row in rows],
            [strong.aggregate_id, mild.aggregate_id],
        )
        self.assertGreater(rows[0]["risk"], rows[1]["risk"])
        self.assertEqual(self.store.count_results(), 2)
        self.assertEqual(self.store.count_results(route="auto"), 1)

    def test_queue_can_list_high_confidence_results_waiting_for_confirmation(self):
        auto = self.save_high_confidence()
        self.save_needs_review(MILD_DISAGREEMENT)

        rows = self.store.list_results(route="auto")

        self.assertEqual([row["aggregate_id"] for row in rows], [auto.aggregate_id])

    def test_queue_skips_results_that_are_already_final(self):
        auto = self.save_high_confidence()
        self.store.record_review(auto.aggregate_id, decision="accept")

        self.assertEqual(self.store.list_results(route="auto"), [])
        finalized = self.store.list_results(route="auto", pending=False)
        self.assertEqual([row["aggregate_id"] for row in finalized], [auto.aggregate_id])
        self.assertTrue(finalized[0]["reviewed"])

    def test_queue_groups_by_question_before_sorting_by_risk(self):
        self.save_needs_review(MILD_DISAGREEMENT)  # Q-01，risk 较低
        other = from_dict(RUBRIC_DATA)
        other.question_id = "Q-02"
        self.save_needs_review(STRONG_DISAGREEMENT, rubric=other)  # 另一题的 risk 更高，但不可比

        rows = self.store.list_results()

        self.assertEqual([row["question_code"] for row in rows], ["Q-01", "Q-02"])

    def test_queue_can_be_narrowed_to_one_question(self):
        self.save_needs_review(MILD_DISAGREEMENT)

        self.assertEqual(len(self.store.list_results(question_code="Q-01")), 1)
        self.assertEqual(self.store.list_results(question_code="Q-99"), [])


class ReviewingOneResult(ReviewStoreTestCase):
    def test_accept_keeps_the_model_score_and_records_the_reviewer(self):
        stored = self.save_needs_review(MILD_DISAGREEMENT)

        review = self.store.record_review(
            stored.aggregate_id, decision="accept", comment="逐条看过，没有编造证据"
        )

        self.assertEqual(review.decision, "accept")
        self.assertEqual(review.final_score, stored.evaluation.aggregate.score)
        self.assertEqual(review.reviewer_id, "admin")
        self.assertEqual(review.comment, "逐条看过，没有编造证据")

    def test_override_records_the_new_score_and_comment(self):
        stored = self.save_needs_review(MILD_DISAGREEMENT)

        review = self.store.record_review(
            stored.aggregate_id,
            decision="override",
            final_score=5.5,
            comment="P2 的证据只覆盖半个要点",
        )

        self.assertEqual(review.final_score, 5.5)
        loaded = self.store.load_review(stored.aggregate_id)
        self.assertEqual(loaded.final_score, 5.5)
        self.assertEqual(loaded.decision, "override")
        # 复核只追加记录，不改写模型算出来的原分
        loaded_evaluation = self.store.load_evaluation(aggregate_id=stored.aggregate_id)
        self.assertEqual(loaded_evaluation.evaluation.aggregate.score, 7.0)

    def test_override_requires_a_comment(self):
        stored = self.save_needs_review(MILD_DISAGREEMENT)

        with self.assertRaises(ReviewRejected):
            self.store.record_review(
                stored.aggregate_id, decision="override", final_score=5.5, comment="   "
            )

    def test_override_outside_the_score_range_is_rejected(self):
        stored = self.save_needs_review(MILD_DISAGREEMENT)

        for bad in (-0.5, 10.5, float("nan"), float("inf")):
            with self.subTest(score=bad), self.assertRaises(ReviewRejected):
                self.store.record_review(
                    stored.aggregate_id,
                    decision="override",
                    final_score=bad,
                    comment="越界",
                )

    def test_accept_cannot_smuggle_in_a_different_score(self):
        stored = self.save_needs_review(MILD_DISAGREEMENT)

        with self.assertRaises(ReviewRejected):
            self.store.record_review(
                stored.aggregate_id, decision="accept", final_score=9.0, comment=""
            )

    def test_second_review_of_the_same_result_is_refused(self):
        stored = self.save_needs_review(MILD_DISAGREEMENT)
        self.store.record_review(stored.aggregate_id, decision="accept")

        with self.assertRaises(ResultFinalized):
            self.store.record_review(stored.aggregate_id, decision="accept")

    def test_reviewing_a_result_that_does_not_exist_is_reported(self):
        with self.assertRaises(ResultNotFound):
            self.store.record_review(9999, decision="accept")


class BatchPass(ReviewStoreTestCase):
    def test_batch_pass_finalizes_every_selected_high_confidence_result(self):
        first = self.save_high_confidence()
        second = self.save_high_confidence()

        reviews = self.store.accept_high_confidence([first.aggregate_id, second.aggregate_id])

        self.assertEqual(len(reviews), 2)
        self.assertTrue(all(review.decision == "accept" for review in reviews))
        self.assertEqual(self.store.list_results(route="auto"), [])

    def test_batch_pass_refuses_when_any_result_needs_review(self):
        auto = self.save_high_confidence()
        human = self.save_needs_review(MILD_DISAGREEMENT)

        with self.assertRaises(ReviewRejected):
            self.store.accept_high_confidence([auto.aggregate_id, human.aggregate_id])

        # 全有或全无：一份不合格，整批不落库
        self.assertIsNone(self.store.load_review(auto.aggregate_id))

    def test_batch_pass_refuses_an_already_finalized_result(self):
        auto = self.save_high_confidence()
        self.store.record_review(auto.aggregate_id, decision="accept")

        with self.assertRaises(ResultFinalized):
            self.store.accept_high_confidence([auto.aggregate_id])

    def test_batch_pass_rejects_unknown_ids(self):
        with self.assertRaises(ReviewRejected):
            self.store.accept_high_confidence([9999])

    def test_batch_pass_refuses_a_result_that_was_regraded(self):
        superseded = self.save_high_confidence()
        evaluation = evaluate(self.rubric, ANSWER, CannedProvider([payload_text()]), samples=1)
        self.store.save_evaluation_for_answer(
            answer_id=superseded.answer_id, rubric=self.rubric, evaluation=evaluation
        )

        with self.assertRaises(ResultSuperseded):
            self.store.accept_high_confidence([superseded.aggregate_id])


class FinalizedSnapshots(ReviewStoreTestCase):
    def test_finalized_result_keeps_its_rubric_after_the_standard_changes(self):
        stored = self.save_high_confidence()
        self.store.record_review(stored.aggregate_id, decision="accept", comment="定稿")

        changed = from_dict(RUBRIC_DATA)
        changed.items[0].desc = "改过的采分点描述"
        self.save(
            rubric=changed,
            answer_text="改标准之后录入的另一份答卷",
            responses=[payload_text()],
        )

        loaded = self.store.load_evaluation(aggregate_id=stored.aggregate_id)
        self.assertEqual(loaded.rubric.items[0].desc, "客户端发送 SYN 及初始序列号")
        self.assertEqual(self.store.load_review(stored.aggregate_id).final_score, 8.0)

    def test_result_can_be_loaded_by_aggregate_id(self):
        stored = self.save_high_confidence()

        loaded = self.store.load_evaluation(aggregate_id=stored.aggregate_id)

        self.assertEqual(loaded.aggregate_id, stored.aggregate_id)
        self.assertEqual(loaded.answer_id, stored.answer_id)
        self.assertEqual(loaded.evaluation.aggregate.score, stored.evaluation.aggregate.score)

    def test_queue_hides_a_result_that_was_regraded(self):
        previous = self.save_needs_review(MILD_DISAGREEMENT)
        evaluation = evaluate(self.rubric, ANSWER, CannedProvider([payload_text()]), samples=1)
        current = self.store.save_evaluation_for_answer(
            answer_id=previous.answer_id, rubric=self.rubric, evaluation=evaluation
        )

        self.assertEqual(self.store.list_results(), [])
        self.assertEqual(
            [row["aggregate_id"] for row in self.store.list_results(route=None)],
            [current.aggregate_id],
        )

    def test_a_superseded_result_cannot_be_finalized(self):
        previous = self.save_needs_review(MILD_DISAGREEMENT)
        evaluation = evaluate(self.rubric, ANSWER, CannedProvider([payload_text()]), samples=1)
        self.store.save_evaluation_for_answer(
            answer_id=previous.answer_id, rubric=self.rubric, evaluation=evaluation
        )

        with self.assertRaises(ResultSuperseded):
            self.store.record_review(previous.aggregate_id, decision="accept")

    def test_a_finalized_answer_cannot_be_regraded(self):
        stored = self.save_high_confidence()
        self.store.record_review(stored.aggregate_id, decision="accept")

        evaluation = evaluate(self.rubric, ANSWER, CannedProvider([payload_text()]), samples=1)
        with self.assertRaises(ResultFinalized):
            self.store.save_evaluation_for_answer(
                answer_id=stored.answer_id, rubric=self.rubric, evaluation=evaluation
            )

    def test_a_finalized_answer_cannot_join_a_new_task(self):
        stored = self.save_high_confidence()
        self.store.record_review(stored.aggregate_id, decision="accept")

        with self.assertRaises(ValueError):
            self.store.create_task(
                question_code="Q-01", answer_ids=[stored.answer_id], samples=1
            )

    def test_loading_requires_exactly_one_key(self):
        with self.assertRaises(ValueError):
            self.store.load_evaluation()
        with self.assertRaises(ValueError):
            self.store.load_evaluation(answer_id=1, aggregate_id=2)


if __name__ == "__main__":
    unittest.main()
