"""合成用例与对照实验：校验、四组配置、指标。

模型一律用 CannedProvider，不联网；这层测的是"用例怎么解释、指标怎么算"，
不是模型判得准不准。
"""

import json
import unittest

from app.core.rubric import from_dict
from app.experiment import (
    CONFIG_KEYS,
    Case,
    CaseFileError,
    CaseSet,
    check_cases,
    config_for,
    parse_case_set,
    quadratic_weighted_kappa,
    run_config,
)
from app.providers.fake import CannedProvider
from tests.fixtures import ANSWER, RUBRIC_DATA, payload_text, payload_with_levels

RUBRIC = from_dict(RUBRIC_DATA)
FULL = {"P1": 1.0, "P2": 1.0, "P3": 1.0}


def case(
    case_id: str = "perfect",
    *,
    total: float = 10,
    levels: dict[str, float] | None = None,
    correct: bool = True,
    answer: str = ANSWER,
) -> Case:
    return Case(
        case_id=case_id,
        answer=answer,
        expected_total=total,
        expected_items=dict(levels or FULL),
        correct=correct,
    )


def case_set(*cases: Case) -> CaseSet:
    return CaseSet(question_code="Q-01", cases=list(cases))


def codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def fake_evidence_payload(*levels: float) -> str:
    """等级拉满、证据却不在原文里：用于区分 B2（不校验）与 B3（校验）。"""
    data = json.loads(payload_with_levels(*levels))
    for entry in data["items"]:
        entry["evidence"] = "这句话根本不在答卷里"
    return json.dumps(data, ensure_ascii=False)


class Validation(unittest.TestCase):
    def test_a_valid_case_set_with_a_correct_case_passes(self):
        cases = case_set(
            case("perfect", total=10, correct=True),
            case("weak", total=3.5, levels={"P1": 0.5, "P2": 0.5, "P3": 0.0}, correct=False),
        )

        self.assertEqual(check_cases(cases, RUBRIC), [])

    def test_at_least_one_correct_answer_case_is_required(self):
        cases = case_set(
            case("weak", total=3.5, levels={"P1": 0.5, "P2": 0.5, "P3": 0.0}, correct=False)
        )

        self.assertIn("NO_CORRECT_CASE", codes(check_cases(cases, RUBRIC)))

    def test_correct_case_must_really_be_full_marks(self):
        cases = case_set(case("not-really", total=7, levels={"P1": 1, "P2": 1, "P3": 0}))

        self.assertIn("CORRECT_CASE_NOT_FULL", codes(check_cases(cases, RUBRIC)))

    def test_unknown_items_and_levels_outside_the_rubric_are_reported(self):
        cases = case_set(
            Case(
                case_id="bad",
                answer=ANSWER,
                expected_total=10,
                expected_items={"P1": 1, "P2": 1, "P3": 9, "P9": 1},
                correct=True,
            )
        )

        found = codes(check_cases(cases, RUBRIC))

        self.assertIn("LEVEL_NOT_ALLOWED", found)
        self.assertIn("UNKNOWN_ITEM", found)

    def test_total_outside_range_and_wrong_question_are_reported(self):
        cases = CaseSet(
            question_code="Q-02",
            cases=[case("too-high", total=11)],
        )

        found = codes(check_cases(cases, RUBRIC))

        self.assertIn("TOTAL_OUT_OF_RANGE", found)
        self.assertIn("QUESTION_MISMATCH", found)

    def test_question_existence_can_be_checked_without_touching_a_model(self):
        cases = case_set(case())

        found = codes(check_cases(cases, RUBRIC, question_exists=False))

        self.assertEqual(found, {"QUESTION_NOT_FOUND"})

    def test_parse_rejects_missing_fields(self):
        with self.assertRaises(CaseFileError):
            parse_case_set({"question_code": "Q-01", "cases": [{"id": "x"}]})


class Configs(unittest.TestCase):
    def test_four_comparison_configs_are_available(self):
        self.assertEqual(CONFIG_KEYS, ("b0", "b2", "b3", "b5"))
        self.assertTrue(config_for("b0").direct)
        self.assertFalse(config_for("b2").grounding)
        self.assertTrue(config_for("b3").grounding)
        self.assertTrue(config_for("b5").routing)
        self.assertEqual(config_for("b5", samples=5).samples, 5)

    def test_b0_reports_a_total_without_item_accuracy(self):
        cases = case_set(case(total=10))
        result = run_config(
            RUBRIC,
            cases,
            config_for("b0"),
            lambda: CannedProvider(['{"total": 8}']),
        )

        self.assertEqual(result.outcomes[0].predicted_total, 8)
        self.assertFalse(result.outcomes[0].passed)
        self.assertIsNone(result.metrics.item_accuracy)
        self.assertIsNone(result.metrics.grounding_rate)
        self.assertIn("总分", result.failures[0])

    def test_b2_accepts_fabricated_evidence_while_b3_does_not(self):
        raw = fake_evidence_payload(1, 1, 1)
        cases = case_set(case(total=10))

        without_grounding = run_config(
            RUBRIC, cases, config_for("b2"), lambda: CannedProvider([raw])
        )
        with_grounding = run_config(
            RUBRIC, cases, config_for("b3"), lambda: CannedProvider([raw])
        )

        self.assertEqual(without_grounding.outcomes[0].predicted_total, 10)
        self.assertIsNone(without_grounding.metrics.grounding_rate)
        self.assertEqual(with_grounding.outcomes[0].predicted_total, 0)
        self.assertEqual(with_grounding.metrics.grounding_rate, 0.0)

    def test_metrics_are_computed_over_all_cases(self):
        cases = case_set(
            case("a", total=10, levels={"P1": 1, "P2": 1, "P3": 1}),
            case("b", total=7, levels={"P1": 1, "P2": 1, "P3": 0}, correct=False),
            case("c", total=3.5, levels={"P1": 0.5, "P2": 0.5, "P3": 0}, correct=False),
            case("d", total=0, levels={"P1": 0, "P2": 0, "P3": 0}, correct=False),
        )
        responses = iter(
            [
                payload_with_levels(1, 1, 1),
                payload_with_levels(1, 1, 0),
                payload_with_levels(0.5, 0.5, 0),
                payload_with_levels(0, 0, 0),
            ]
        )

        def factory():
            return CannedProvider([next(responses)])

        result = run_config(RUBRIC, cases, config_for("b2"), factory)

        self.assertEqual(result.metrics.passed, 4)
        self.assertEqual(result.metrics.pass_rate, 1.0)
        self.assertEqual(result.metrics.total_mae, 0.0)
        self.assertEqual(result.metrics.qwk, 1.0)
        self.assertEqual(result.metrics.item_accuracy, 1.0)
        self.assertIsNone(result.metrics.auto_pass_rate)

    def test_b5_reports_grounding_and_auto_pass_rate(self):
        cases = case_set(
            case("perfect", total=10),
            case("weak", total=8, levels={"P1": 1, "P2": 0.5, "P3": 1}, correct=False),
        )
        responses = iter([payload_with_levels(1, 1, 1), payload_text()])

        def factory():
            return CannedProvider([next(responses)])

        result = run_config(RUBRIC, cases, config_for("b5", samples=1), factory)

        self.assertEqual(result.metrics.grounding_rate, 1.0)
        # 满分边界会转人工，所以两份里只有一份自动通过
        self.assertEqual(result.metrics.auto_pass_rate, 0.5)


class Metrics(unittest.TestCase):
    def test_qwk_is_one_for_perfect_agreement(self):
        self.assertEqual(
            quadratic_weighted_kappa([10, 7, 3.5, 0], [10, 7, 3.5, 0]), 1.0
        )

    def test_qwk_is_undefined_with_fewer_than_two_cases(self):
        self.assertIsNone(quadratic_weighted_kappa([10], [10]))


if __name__ == "__main__":
    unittest.main()
