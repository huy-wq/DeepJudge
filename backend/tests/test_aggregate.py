import json
import unittest

from app.core.aggregate import aggregate
from app.core.routing import RoutePolicy, decide
from app.core.rubric import from_dict
from app.pipeline import evaluate, grade
from app.providers.fake import CannedProvider
from tests.fixtures import ANSWER, RUBRIC_DATA

EVIDENCE = {
    "P1": "客户端首先发送 SYN 报文，seq=x",
    "P2": "服务器回复 SYN=1、ACK=1，seq=y，ack=x+1",
    "P3": "客户端再发送 ACK=1，seq=x+1，ack=y+1",
}


def sample_payload(levels, evidence=None, deduction_hit=False):
    evidence = evidence or {}
    items = []
    for item_id, level in levels.items():
        items.append(
            {
                "id": item_id,
                "hit": level > 0,
                "level": level,
                "evidence": evidence.get(item_id, EVIDENCE[item_id]),
                "reason": "测试",
            }
        )
    return json.dumps(
        {
            "items": items,
            "deductions": [
                {
                    "id": "D1",
                    "hit": deduction_hit,
                    "evidence": "三次握手用于同步双方初始序列号" if deduction_hit else "",
                }
            ],
            "comment": "测试",
        },
        ensure_ascii=False,
    )


class AggregateThreeSamples(unittest.TestCase):
    def setUp(self):
        self.rubric = from_dict(RUBRIC_DATA)

    def grades(self, level_sets, evidence_overrides=None, deduction_hit=False):
        raws = [
            sample_payload(
                levels,
                evidence=(evidence_overrides or [None] * len(level_sets))[i],
                deduction_hit=deduction_hit,
            )
            for i, levels in enumerate(level_sets)
        ]
        provider = CannedProvider(raws)
        return [grade(self.rubric, ANSWER, provider) for _ in raws]

    def test_median_levels_drive_a_recomputed_total(self):
        samples = self.grades(
            [
                {"P1": 1.0, "P2": 0.5, "P3": 1.0},
                {"P1": 0.5, "P2": 0.5, "P3": 1.0},
                {"P1": 1.0, "P2": 0.5, "P3": 1.0},
            ]
        )
        result = aggregate(self.rubric, samples)

        self.assertEqual(result.levels, {"P1": 1.0, "P2": 0.5, "P3": 1.0})
        self.assertEqual(result.score, 8.0)
        self.assertEqual(result.sample_totals, [8.0, 6.5, 8.0])
        self.assertEqual(result.dispersion, 0.15)
        self.assertAlmostEqual(result.confidence, 0.85)
        self.assertEqual(result.total_range, 1.5)

    def test_agreement_gives_zero_dispersion_and_auto_route(self):
        agreeing = [{"P1": 1.0, "P2": 0.5, "P3": 1.0}] * 3
        samples = self.grades(agreeing)
        result = aggregate(self.rubric, samples)
        decision = decide(result)

        self.assertEqual(result.dispersion, 0.0)
        self.assertEqual(result.confidence, 1.0)
        self.assertEqual(result.total_range, 0.0)
        self.assertEqual(decision.route, "auto")
        self.assertEqual(decision.reasons, [])

    def test_disagreement_raises_dispersion_and_forces_review(self):
        samples = self.grades(
            [
                {"P1": 1.0, "P2": 0.5, "P3": 1.0},
                {"P1": 0.0, "P2": 0.5, "P3": 1.0},
                {"P1": 1.0, "P2": 0.5, "P3": 1.0},
            ]
        )
        result = aggregate(self.rubric, samples)
        decision = decide(result)

        self.assertEqual(result.levels["P1"], 1.0, "三个等级取中位数")
        self.assertGreater(result.dispersion, 0.0)
        self.assertLess(result.confidence, 1.0)
        self.assertEqual(decision.route, "human")
        self.assertTrue(any("置信度" in r for r in decision.reasons), decision.reasons)
        self.assertTrue(any("极差" in r for r in decision.reasons), decision.reasons)

    def test_ungrounded_claim_counts_as_zero_and_lowers_confidence(self):
        samples = self.grades(
            [{"P1": 1.0, "P2": 1.0, "P3": 1.0}] * 3,
            evidence_overrides=[None, {"P2": "学生使用了三次挥手断开连接"}, None],
        )
        result = aggregate(self.rubric, samples)
        decision = decide(result)

        self.assertEqual(result.sample_totals[1], 6.0, "未接地的 P2 按 0 分计")
        self.assertGreater(result.dispersion, 0.0)
        self.assertLess(result.grounding_rate, 1.0)
        self.assertEqual(decision.route, "human")
        self.assertTrue(any("未接地" in r for r in decision.reasons), decision.reasons)

    def test_boundary_score_forces_review(self):
        samples = self.grades([{"P1": 1.0, "P2": 1.0, "P3": 1.0}] * 3)
        result = aggregate(self.rubric, samples)
        decision = decide(result)

        self.assertEqual(result.score, 10.0)
        self.assertEqual(decision.route, "human")
        self.assertTrue(any("边界" in r for r in decision.reasons), decision.reasons)

    def test_policy_is_configurable(self):
        samples = self.grades(
            [{"P1": 1.0, "P2": 1.0, "P3": 1.0}] * 3
        )
        result = aggregate(self.rubric, samples)
        decision = decide(result, RoutePolicy(full_marks_margin=0.0))

        self.assertEqual(decision.route, "auto")


class SamplingIsConfigurable(unittest.TestCase):
    def test_evaluate_runs_the_requested_number_of_samples(self):
        rubric = from_dict(RUBRIC_DATA)
        raw = sample_payload({"P1": 1.0, "P2": 1.0, "P3": 1.0})
        provider = CannedProvider([raw])

        result = evaluate(rubric, ANSWER, provider, samples=5)

        self.assertEqual(len(result.samples), 5)
        self.assertEqual(result.aggregate.score, 10.0)




class ConfidenceReflectsGrounding(unittest.TestCase):
    def setUp(self):
        self.rubric = from_dict(RUBRIC_DATA)

    def test_all_ungrounded_claims_collapse_confidence(self):
        raws = [
            sample_payload(
                {"P1": 1.0, "P2": 1.0, "P3": 1.0},
                evidence={"P1": "编造一", "P2": "编造二", "P3": "编造三"},
            )
        ] * 3
        provider = CannedProvider(raws)
        samples = [grade(self.rubric, ANSWER, provider) for _ in raws]
        result = aggregate(self.rubric, samples)
        decision = decide(result)

        self.assertEqual(result.grounding_rate, 0.0)
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.score, 0.0)
        self.assertEqual(decision.route, "human")

    def test_deduction_only_disagreement_raises_dispersion(self):
        raws = [
            sample_payload({"P1": 1.0, "P2": 1.0, "P3": 1.0}, deduction_hit=True),
            sample_payload({"P1": 1.0, "P2": 1.0, "P3": 1.0}, deduction_hit=False),
            sample_payload({"P1": 1.0, "P2": 1.0, "P3": 1.0}, deduction_hit=True),
        ]
        provider = CannedProvider(raws)
        samples = [grade(self.rubric, ANSWER, provider) for _ in raws]
        result = aggregate(self.rubric, samples)

        self.assertGreater(result.dispersion, 0.0)
        self.assertLess(result.confidence, 1.0)


class AllSamplesDisagree(unittest.TestCase):
    def test_every_sample_differs_forces_review(self):
        rubric = from_dict(RUBRIC_DATA)
        raws = [
            sample_payload({"P1": 1.0, "P2": 1.0, "P3": 1.0}),
            sample_payload({"P1": 0.5, "P2": 0.5, "P3": 0.0}),
            sample_payload({"P1": 0.0, "P2": 0.0, "P3": 0.0}),
        ]
        provider = CannedProvider(raws)
        samples = [grade(rubric, ANSWER, provider) for _ in raws]

        result = aggregate(rubric, samples)
        decision = decide(result)

        self.assertEqual(len({tuple(sorted(s.levels().items())) for s in samples}), 3)
        self.assertEqual(decision.route, "human")
        self.assertGreater(result.total_range, 1.0)
        self.assertTrue(any("极差" in reason for reason in decision.reasons), decision.reasons)


if __name__ == "__main__":
    unittest.main()
