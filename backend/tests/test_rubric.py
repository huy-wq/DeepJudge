import copy
import json
import unittest

from app.core.rubric import RubricError, from_dict, to_dict
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


if __name__ == "__main__":
    unittest.main()
