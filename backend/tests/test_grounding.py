import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.cli import main
from app.core.grounding import locate_evidence
from app.core.rubric import from_dict
from app.pipeline import grade
from app.providers.fake import CannedProvider
from tests.fixtures import ANSWER, RUBRIC_DATA
from tests.test_pipeline import SAMPLE_PAYLOAD


def payload_with(item_id: str, evidence: str, hit: bool = True) -> str:
    data = json.loads(json.dumps(SAMPLE_PAYLOAD))
    for item in data["items"]:
        if item["id"] == item_id:
            item["evidence"] = evidence
            item["hit"] = hit
    return json.dumps(data, ensure_ascii=False)


class GroundingCase(unittest.TestCase):
    def setUp(self):
        self.rubric = from_dict(RUBRIC_DATA)

    def grade_with(self, text: str):
        return grade(self.rubric, ANSWER, CannedProvider([text]))


class LocateEvidence(unittest.TestCase):
    def test_quoted_evidence_returns_exact_offsets(self):
        span = locate_evidence(ANSWER, "客户端首先发送 SYN 报文，seq=x")
        self.assertIsNotNone(span)
        start, end = span
        self.assertEqual(ANSWER[start:end], "客户端首先发送 SYN 报文，seq=x")

    def test_one_typo_in_long_evidence_still_locates(self):
        span = locate_evidence(ANSWER, "服务器回复 SYN=1、ACK=1，seq=y，ack=x+9")
        self.assertIsNotNone(span)

    def test_one_typo_in_short_evidence_still_locates(self):
        span = locate_evidence(ANSWER, "ACK=2")
        self.assertIsNotNone(span, "短证据允许 1 个字符的偏差")

    def test_words_scattered_across_the_answer_do_not_count(self):
        self.assertIsNone(locate_evidence(ANSWER, "客户端三次握手"))

    def test_unrelated_text_is_rejected(self):
        self.assertIsNone(locate_evidence(ANSWER, "学生使用三次挥手完成断开"))

    def test_full_width_punctuation_difference_is_normalised(self):
        self.assertIsNotNone(locate_evidence(ANSWER, "服务器回复 SYN=1,ACK=1,seq=y,ack=x+1"))


class GroundingInGrading(GroundingCase):
    def test_located_evidence_marks_item_grounded_with_span(self):
        result = self.grade_with(payload_with("P1", "客户端首先发送 SYN 报文，seq=x"))
        judgement = result.items[0]

        self.assertTrue(judgement.grounded)
        start, end = judgement.span
        self.assertEqual(ANSWER[start:end], "客户端首先发送 SYN 报文，seq=x")

    def test_invented_evidence_scores_zero_but_keeps_the_claim(self):
        result = self.grade_with(payload_with("P3", "学生使用三次挥手完成断开"))
        judgement = result.items[2]

        self.assertFalse(judgement.grounded)
        self.assertTrue(judgement.hit, "hit 是模型的声称，审计时要保留")
        self.assertEqual(judgement.level, 0.0)
        self.assertEqual(result.score, 5.0)
        self.assertIn("定位", judgement.reason)
        self.assertEqual(result.ungrounded_ids(), ["P3"])

    def test_hit_without_evidence_scores_zero(self):
        result = self.grade_with(payload_with("P2", ""))
        self.assertFalse(result.items[1].grounded)
        self.assertEqual(result.items[1].level, 0.0)

    def test_grounding_rate_counts_only_claimed_judgements(self):
        result = self.grade_with(payload_with("P3", "学生使用三次挥手完成断开"))
        self.assertAlmostEqual(result.grounding_rate(), 2 / 3)

    def test_grounding_rate_is_one_when_nothing_is_claimed(self):
        data = json.loads(payload_with("P1", ""))
        for item in data["items"]:
            item["hit"] = False
        result = self.grade_with(json.dumps(data, ensure_ascii=False))
        self.assertEqual(result.grounding_rate(), 1.0)


class DeductionGrounding(GroundingCase):
    def test_located_deduction_evidence_keeps_the_deduction(self):
        data = json.loads(payload_with("P1", "客户端首先发送 SYN 报文，seq=x"))
        data["deductions"][0].update(hit=True, evidence="三次握手用于同步双方初始序列号")
        result = self.grade_with(json.dumps(data, ensure_ascii=False))

        self.assertTrue(result.deductions[0].grounded)
        self.assertIsNotNone(result.deductions[0].span)
        self.assertEqual(result.score, 6.0)

    def test_unsupported_deduction_is_not_applied(self):
        data = json.loads(payload_with("P1", "客户端首先发送 SYN 报文，seq=x"))
        data["deductions"][0].update(hit=True, evidence="学生把三次握手说成四次挥手")
        result = self.grade_with(json.dumps(data, ensure_ascii=False))

        self.assertFalse(result.deductions[0].grounded)
        self.assertTrue(result.deductions[0].hit)
        self.assertEqual(result.score, 8.0)
        self.assertEqual(result.ungrounded_ids(), ["D1"])


class GroundingInCliReport(GroundingCase):
    def run_cli(self, model_text: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rubric.json").write_text(
                json.dumps(RUBRIC_DATA, ensure_ascii=False), encoding="utf-8"
            )
            (root / "answer.txt").write_text(ANSWER, encoding="utf-8")
            (root / "model.json").write_text(model_text, encoding="utf-8")

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main([
                    "--rubric", str(root / "rubric.json"),
                    "--answer", str(root / "answer.txt"),
                    "--response", str(root / "model.json"),
                ])
        return code, json.loads(buffer.getvalue())

    def test_report_exposes_spans_rate_and_ungrounded_ids(self):
        code, report = self.run_cli(payload_with("P1", "客户端首先发送 SYN 报文，seq=x"))
        self.assertEqual(code, 0)
        self.assertEqual(report["grounding_rate"], 1.0)
        self.assertEqual(report["ungrounded"], [])
        self.assertEqual(len(report["items"][0]["span"]), 2)

    def test_report_lists_ungrounded_judgements(self):
        code, report = self.run_cli(payload_with("P3", "学生使用三次挥手完成断开"))
        self.assertEqual(code, 0)
        self.assertEqual(report["ungrounded"], ["P3"])
        self.assertAlmostEqual(report["grounding_rate"], 0.6667)


if __name__ == "__main__":
    unittest.main()
