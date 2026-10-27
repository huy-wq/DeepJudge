import copy
import json
import unittest

from app.core.rubric import RubricError, from_dict, parse, to_dict, validate
from tests.fixtures import RUBRIC_DATA


class LoadValidRubric(unittest.TestCase):
    def test_loads_items_and_deduction(self):
        rubric = from_dict(RUBRIC_DATA)
        self.assertEqual(rubric.total_score, 10)
        self.assertEqual([it.id for it in rubric.items], ["P1", "P2", "P3"])
        self.assertEqual(rubric.item("P2").score_for(0.5), 2.0)


class RejectInvalidRubric(unittest.TestCase):
    def problems_for(self, mutate):
        data = copy.deepcopy(RUBRIC_DATA)
        mutate(data)
        with self.assertRaises(RubricError) as ctx:
            from_dict(data)
        return ctx.exception.problems

    def test_weight_sum_must_equal_total(self):
        problems = self.problems_for(lambda d: d.update(total_score=11))
        self.assertTrue(any("权重合计" in p for p in problems), problems)

    def test_ids_must_be_unique(self):
        problems = self.problems_for(lambda d: d["items"][1].update(id="P1"))
        self.assertTrue(any("id 重复" in p for p in problems), problems)

    def test_level_score_must_stay_within_weight(self):
        problems = self.problems_for(
            lambda d: d["items"][0]["levels"][0].update(score=5.0)
        )
        self.assertTrue(any("超出" in p for p in problems), problems)

    def test_level_scores_must_not_decrease_with_level(self):
        problems = self.problems_for(
            lambda d: d["items"][0]["levels"][0].update(score=1.0)
        )
        self.assertTrue(any("下降" in p for p in problems), problems)

    def test_deduction_cap_must_not_be_looser_than_penalty(self):
        problems = self.problems_for(lambda d: d["deductions"][0].update(cap=1))
        self.assertTrue(any("封顶" in p for p in problems), problems)

    def test_reports_every_problem_at_once(self):
        def mutate(d):
            d["total_score"] = 11
            d["deductions"][0]["cap"] = 1
            d["items"][2]["id"] = "P1"

        problems = self.problems_for(mutate)
        self.assertGreaterEqual(len(problems), 3, problems)


class CollectEveryStructuralProblem(unittest.TestCase):
    def test_missing_field_and_semantic_problems_are_reported_together(self):
        data = copy.deepcopy(RUBRIC_DATA)
        del data["total_score"]
        data["items"][1]["id"] = "P1"
        data["items"][2]["levels"][0]["score"] = 9.0

        with self.assertRaises(RubricError) as ctx:
            from_dict(data)

        problems = ctx.exception.problems
        self.assertTrue(any("total_score" in p for p in problems), problems)
        self.assertTrue(any("id 重复" in p for p in problems), problems)
        self.assertTrue(any("超出" in p for p in problems), problems)

    def test_item_with_broken_levels_is_reported_by_id(self):
        data = copy.deepcopy(RUBRIC_DATA)
        data["items"][0]["levels"] = [{"level": 1.0}]

        with self.assertRaises(RubricError) as ctx:
            from_dict(data)

        self.assertTrue(any("P1" in p and "score" in p for p in ctx.exception.problems))


class SerializeRubric(unittest.TestCase):
    def test_to_dict_round_trips_through_from_dict(self):
        original = from_dict(copy.deepcopy(RUBRIC_DATA))
        restored = from_dict(to_dict(original))
        self.assertEqual(restored, original)

    def test_to_dict_output_is_plain_json_data(self):
        payload = to_dict(from_dict(copy.deepcopy(RUBRIC_DATA)))
        self.assertEqual(json.loads(json.dumps(payload)), payload)


class ClassifyIssues(unittest.TestCase):
    """校验清单要把"阻断保存"和"仅提示"分开（架构 §5.3）。"""

    def setUp(self):
        self.rubric = from_dict(copy.deepcopy(RUBRIC_DATA))

    def codes(self, rubric):
        return {issue.code: issue for issue in validate(rubric)}

    def test_valid_rubric_has_no_issue_at_all(self):
        self.assertEqual(validate(self.rubric), [])

    def test_weight_shortfall_reports_the_exact_gap(self):
        data = copy.deepcopy(RUBRIC_DATA)
        data["total_score"] = 13          # 权重合计 10，缺 3 分
        with self.assertRaises(RubricError) as ctx:
            from_dict(data)

        issue = next(i for i in ctx.exception.issues if i.code == "WEIGHT_SUM")
        self.assertTrue(issue.blocking)
        self.assertIn("3 分未分配", issue.message)

    def test_weight_overflow_reports_the_exact_gap(self):
        data = copy.deepcopy(RUBRIC_DATA)
        data["total_score"] = 8           # 权重合计 10，超 2 分
        with self.assertRaises(RubricError) as ctx:
            from_dict(data)

        issue = next(i for i in ctx.exception.issues if i.code == "WEIGHT_SUM")
        self.assertIn("超出 2 分", issue.message)

    def test_top_level_below_weight_is_a_warning_and_does_not_block(self):
        data = copy.deepcopy(RUBRIC_DATA)
        data["items"][0]["levels"][0]["score"] = 2.5   # 权重 3 的点最高只能拿 2.5

        rubric = from_dict(data)                        # 不抛：只是警告

        issue = self.codes(rubric)["TOP_LEVEL_BELOW_WEIGHT"]
        self.assertFalse(issue.blocking)
        self.assertIn("满分不可达", issue.message)

    def test_empty_and_vague_criteria_are_warnings(self):
        data = copy.deepcopy(RUBRIC_DATA)
        data["items"][0]["levels"][0]["criterion"] = ""
        data["items"][1]["levels"][0]["criterion"] = "论述充分即可"

        codes = self.codes(from_dict(data))

        self.assertFalse(codes["EMPTY_CRITERION"].blocking)
        self.assertFalse(codes["VAGUE_CRITERION"].blocking)
        self.assertIn("论述充分", codes["VAGUE_CRITERION"].message)

    def test_duplicate_descriptions_are_warned_not_blocked(self):
        data = copy.deepcopy(RUBRIC_DATA)
        data["items"][1]["desc"] = data["items"][0]["desc"]

        codes = self.codes(from_dict(data))

        self.assertFalse(codes["DUPLICATE_DESC"].blocking)
        self.assertIn("P1", codes["DUPLICATE_DESC"].message)

    def test_error_exposes_blocking_and_warning_buckets(self):
        data = copy.deepcopy(RUBRIC_DATA)
        data["total_score"] = 13                       # 阻断项
        data["items"][0]["levels"][0]["criterion"] = "理解到位"   # 仅提示

        with self.assertRaises(RubricError) as ctx:
            from_dict(data)

        error = ctx.exception
        self.assertEqual([i.code for i in error.blocking], ["WEIGHT_SUM"])
        self.assertEqual([i.code for i in error.warnings], ["VAGUE_CRITERION"])
        self.assertEqual(len(error.problems), len(error.issues))


class ParseKeepsEveryIssue(unittest.TestCase):
    def test_parse_returns_rubric_and_all_issues_without_raising(self):
        data = copy.deepcopy(RUBRIC_DATA)
        data["items"][0]["levels"][0]["criterion"] = "论述充分"

        parsed = parse(data)

        self.assertTrue(parsed.usable)
        self.assertEqual([issue.code for issue in parsed.warnings], ["VAGUE_CRITERION"])
        self.assertEqual(parsed.rubric.item("P1").weight, 3)

    def test_not_an_object_is_reported_as_an_issue(self):
        parsed = parse(["不是对象"])

        self.assertFalse(parsed.usable)
        self.assertEqual([issue.code for issue in parsed.blocking], ["NOT_OBJECT"])

    def test_shortfall_survives_even_when_structure_is_broken(self):
        """结构错误时更要报差额：少了采分点，差额正是教师需要的数字。"""
        data = copy.deepcopy(RUBRIC_DATA)
        del data["items"][2]["weight"]          # 结构错误：缺必填字段
        data["total_score"] = 10

        parsed = parse(data)

        codes = {issue.code for issue in parsed.blocking}
        self.assertIn("MISSING_FIELD", codes)
        self.assertIn("WEIGHT_SUM", codes)
        gap = next(i for i in parsed.blocking if i.code == "WEIGHT_SUM")
        self.assertIn("3 分未分配", gap.message)

    def test_fractional_shortfall_keeps_its_direction_and_value(self):
        data = copy.deepcopy(RUBRIC_DATA)
        data["items"][2]["weight"] = 2.996      # 权重合计 9.996，满分 10
        data["items"][2]["levels"][0]["score"] = 2.996

        parsed = parse(data)

        gap = next(i for i in parsed.blocking if i.code == "WEIGHT_SUM")
        self.assertIn("0.004 分未分配", gap.message)
        self.assertNotIn("超出 -0", gap.message)


if __name__ == "__main__":
    unittest.main()
