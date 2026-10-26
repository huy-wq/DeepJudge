import json
import unittest

from app.core.prompt import build_messages
from app.core.rubric import from_dict
from app.pipeline import GradingError, grade
from app.providers.fake import CannedProvider
from tests.fixtures import ANSWER, RUBRIC_DATA, payload_text

class GradeOneAnswer(unittest.TestCase):
    def setUp(self):
        self.rubric = from_dict(RUBRIC_DATA)
        self.provider = CannedProvider([payload_text()])

    def test_returns_per_item_levels_and_evidence(self):
        result = grade(self.rubric, ANSWER, self.provider)
        self.assertEqual(result.levels(), {"P1": 1.0, "P2": 0.5, "P3": 1.0})
        self.assertEqual(result.items[1].reason, "字段完整但未说明同意接收")
        self.assertIn("SYN", result.items[0].evidence)

    def test_computes_total_on_the_server(self):
        result = grade(self.rubric, ANSWER, self.provider)
        self.assertEqual(result.score, 8.0)
        self.assertEqual(result.max_score, 10.0)


class BuildPrompt(unittest.TestCase):
    def test_prompt_is_stable_and_puts_answer_last(self):
        rubric = from_dict(RUBRIC_DATA)
        first = build_messages(rubric, ANSWER)
        second = build_messages(rubric, ANSWER)
        self.assertEqual(first, second)

        joined = "\n".join(m["content"] for m in first)
        self.assertLess(joined.index("P1"), joined.index(ANSWER[:12]))
        self.assertIn("<answer>", joined)


class RejectUnusableOutput(unittest.TestCase):
    def setUp(self):
        self.rubric = from_dict(RUBRIC_DATA)

    def grade_text(self, text):
        return grade(self.rubric, ANSWER, CannedProvider([text]))

    def test_missing_item_is_an_error_not_a_zero(self):
        broken = json.loads(payload_text())
        broken["items"] = [i for i in broken["items"] if i["id"] != "P3"]
        with self.assertRaises(GradingError) as ctx:
            self.grade_text(json.dumps(broken, ensure_ascii=False))
        self.assertIn("P3", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "MISSING_ITEM")

    def test_level_outside_allowed_values_is_rejected(self):
        broken = json.loads(payload_text())
        broken["items"][0]["level"] = 0.7
        with self.assertRaises(GradingError) as ctx:
            self.grade_text(json.dumps(broken, ensure_ascii=False))
        self.assertIn("不在允许值", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "INVALID_LEVEL")

    def test_unparsable_text_is_rejected_with_a_code(self):
        with self.assertRaises(GradingError) as ctx:
            self.grade_text("抱歉，我无法完成该任务。")
        self.assertIn("不是合法 JSON", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "INVALID_JSON")


class IgnoreModelArithmetic(unittest.TestCase):
    def setUp(self):
        self.rubric = from_dict(RUBRIC_DATA)

    def test_model_supplied_score_is_ignored(self):
        result = grade(self.rubric, ANSWER, CannedProvider([payload_text(score=99, max_points=99)]))
        self.assertEqual(result.score, 8.0)

    def test_json_inside_code_fence_is_accepted(self):
        text = "```json\n" + payload_text() + "\n```"
        result = grade(self.rubric, ANSWER, CannedProvider([text]))
        self.assertEqual(result.score, 8.0)

    def test_deduction_reduces_score_and_never_goes_negative(self):
        data = json.loads(payload_text())
        data["deductions"][0].update(hit=True, evidence="客户端再发送 ACK=1")
        result = grade(self.rubric, ANSWER, CannedProvider([json.dumps(data, ensure_ascii=False)]))
        self.assertEqual(result.score, 6.0)

        for item in data["items"]:
            item["level"] = 0.0
        result = grade(self.rubric, ANSWER, CannedProvider([json.dumps(data, ensure_ascii=False)]))
        self.assertEqual(result.score, 0.0)

    def test_missing_deduction_entry_means_not_triggered(self):
        data = json.loads(payload_text())
        data.pop("deductions")
        result = grade(self.rubric, ANSWER, CannedProvider([json.dumps(data, ensure_ascii=False)]))
        self.assertEqual(result.score, 8.0)
        self.assertTrue(all(not dj.hit for dj in result.deductions))


if __name__ == "__main__":
    unittest.main()
