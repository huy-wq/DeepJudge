"""标准生成：提示词、重试、三视图。

接缝是 `generate_rubric(provider, ...)`：provider 换成录制/预设的假实现，全程不联网。
"""

import json
import unittest

from app.providers.fake import CannedProvider
from app.rubric_gen import (
    RubricGenerationError,
    build_generation_messages,
    generate_rubric,
    preview_rows,
)
from tests.fixtures import RUBRIC_DATA

STEM = "简述 TCP 三次握手的过程，并说明其目的。"
STANDARD_TEXT = (
    "三个要点：客户端发 SYN 与初始序列号给 3 分；服务器回 SYN+ACK 给 4 分；"
    "客户端再回 ACK 给 3 分。把三次握手说成四次挥手扣 2 分。"
)


def generation_payload(**overrides) -> str:
    data = {
        "summary": "本题按三次握手的三个步骤拆成三个采分点，另设一个混淆扣分项。",
        "items": json.loads(json.dumps(RUBRIC_DATA["items"])),
        "deductions": json.loads(json.dumps(RUBRIC_DATA["deductions"])),
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class RecordingProvider:
    """记录每次发出的消息，用来断言"重试时说没说该说的话"。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.seen: list[list[dict]] = []

    def complete(self, messages: list[dict], *, sample_index: int = 0) -> str:
        self.seen.append(list(messages))
        return self._responses[min(len(self.seen) - 1, len(self._responses) - 1)]


class GenerationPrompt(unittest.TestCase):
    def setUp(self):
        self.messages = build_generation_messages(
            stem=STEM,
            total_score=10,
            standard_text=STANDARD_TEXT,
            reference_answer="SYN / SYN+ACK / ACK。",
        )

    def test_system_prompt_forbids_inventing_points(self):
        system = self.messages[0]["content"]
        self.assertIn("不得新增教师未提及的采分点", system)
        self.assertIn("禁止", system)

    def test_user_prompt_carries_stem_total_and_teacher_words_verbatim(self):
        content = self.messages[1]["content"]
        self.assertIn(STEM, content)
        self.assertIn("满分：10", content)
        self.assertIn(STANDARD_TEXT, content)
        self.assertIn("<standard>", content)

    def test_teacher_words_are_the_last_thing_before_the_tag(self):
        content = self.messages[1]["content"]
        self.assertTrue(content.rstrip().endswith("</standard>"))


class GenerateValidRubric(unittest.TestCase):
    def setUp(self):
        self.provider = CannedProvider([generation_payload()])

    def generate(self, **kwargs):
        return generate_rubric(
            self.provider,
            stem=STEM,
            total_score=10,
            standard_text=STANDARD_TEXT,
            **kwargs,
        )

    def test_returns_a_usable_rubric(self):
        result = self.generate()

        self.assertEqual(result.rubric.total_score, 10)
        self.assertEqual([item.id for item in result.rubric.items], ["P1", "P2", "P3"])
        self.assertEqual(result.rubric.item("P1").score_for(1.0), 3.0)
        self.assertEqual(result.attempts, 1)

    def test_returns_summary_as_one_of_the_three_views(self):
        result = self.generate()
        self.assertIn("三个采分点", result.summary)

    def test_clean_result_has_no_issue_in_checklist(self):
        self.assertEqual(self.generate().issues, [])

    def test_server_owns_identity_and_total_score(self):
        provider = CannedProvider(
            [
                generation_payload(
                    question_id="HACK",
                    total_score=5,
                    stem="模型偷偷改的题干",
                )
            ]
        )
        result = generate_rubric(
            provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
        )

        self.assertEqual(result.rubric.total_score, 10)
        self.assertEqual(result.rubric.stem, STEM)
        self.assertEqual(result.rubric.question_id, "Q-01")

    def test_summary_falls_back_when_model_omits_it(self):
        provider = CannedProvider([generation_payload(summary="")])
        result = generate_rubric(
            provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
        )

        self.assertIn("3 个采分点", result.summary)
        self.assertIn("满分 10 分", result.summary)


class ThreeViews(unittest.TestCase):
    def test_views_bundle_all_three_in_one_payload(self):
        provider = CannedProvider([generation_payload()])

        views = generate_rubric(
            provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
        ).views()

        self.assertEqual(views["rubric"]["total_score"], 10)
        self.assertIn("三个采分点", views["summary"])
        self.assertEqual([row["id"] for row in views["preview"]], ["P1", "P2", "P3"])
        self.assertEqual(views["checklist"], [])
        self.assertEqual(views["attempts"], 1)

    def test_checklist_entry_carries_code_and_blocking_flag(self):
        data = json.loads(generation_payload())
        data["items"][0]["levels"][0]["criterion"] = "理解到位"
        provider = CannedProvider([json.dumps(data, ensure_ascii=False)])

        checklist = generate_rubric(
            provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
        ).views()["checklist"]

        self.assertEqual(len(checklist), 1)
        self.assertEqual(checklist[0]["code"], "VAGUE_CRITERION")
        self.assertFalse(checklist[0]["blocking"])
        self.assertIn("理解到位", checklist[0]["message"])


class ChecklistSplitsBlockingFromAdvice(unittest.TestCase):
    def test_vague_criterion_is_reported_but_does_not_stop_generation(self):
        data = json.loads(generation_payload())
        data["items"][0]["levels"][0]["criterion"] = "论述充分即可"
        provider = CannedProvider([json.dumps(data, ensure_ascii=False)])

        result = generate_rubric(
            provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
        )

        codes = {issue.code: issue for issue in result.issues}
        self.assertIn("VAGUE_CRITERION", codes)
        self.assertFalse(codes["VAGUE_CRITERION"].blocking)
        self.assertEqual(result.attempts, 1)

    def test_preview_table_lists_every_level_with_score_and_criterion(self):
        provider = CannedProvider([generation_payload()])
        rubric = generate_rubric(
            provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
        ).rubric

        rows = preview_rows(rubric)

        self.assertEqual(len(rows), 3)
        first = rows[0]
        self.assertEqual((first["id"], first["weight"]), ("P1", 3.0))
        self.assertEqual([level["level"] for level in first["levels"]], [1.0, 0.5, 0.0])
        self.assertEqual(first["levels"][1]["score"], 1.5)
        self.assertTrue(first["levels"][0]["criterion"])


class RetryOnceThenFail(unittest.TestCase):
    def test_weight_shortfall_is_reported_as_a_gap_and_not_padded(self):
        data = json.loads(generation_payload())
        data["items"] = [item for item in data["items"] if item["id"] != "P3"]
        provider = RecordingProvider([json.dumps(data, ensure_ascii=False)])

        with self.assertRaises(RubricGenerationError) as ctx:
            generate_rubric(
                provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
            )

        error = ctx.exception
        self.assertEqual(error.attempts, 2)
        self.assertIn("3 分未分配", str(error))
        self.assertEqual([issue.code for issue in error.blocking], ["WEIGHT_SUM"])

    def test_retry_asks_the_model_to_fix_errors_without_inventing_points(self):
        data = json.loads(generation_payload())
        data["items"] = data["items"][:1]
        provider = RecordingProvider([json.dumps(data, ensure_ascii=False)])

        with self.assertRaises(RubricGenerationError):
            generate_rubric(
                provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
            )

        second_turn = provider.seen[1]
        self.assertEqual([message["role"] for message in second_turn], ["system", "user", "assistant", "user"])
        note = second_turn[-1]["content"]
        self.assertIn("不得新增教师未提及的采分点", note)
        self.assertIn("权重", note)

    def test_invalid_json_is_also_retried_once(self):
        provider = RecordingProvider(["抱歉，我无法完成该任务。", "好的。"])

        with self.assertRaises(RubricGenerationError) as ctx:
            generate_rubric(
                provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
            )

        self.assertEqual(ctx.exception.attempts, 2)
        self.assertEqual([issue.code for issue in ctx.exception.blocking], ["INVALID_JSON"])
        self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_recovers_on_the_second_attempt(self):
        data = json.loads(generation_payload())
        data["items"] = data["items"][:2]
        provider = CannedProvider([json.dumps(data, ensure_ascii=False), generation_payload()])

        result = generate_rubric(
            provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
        )

        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(result.responses), 2)
        self.assertEqual(result.rubric.item("P3").weight, 3.0)

    def test_keeps_every_response_for_audit(self):
        provider = CannedProvider(["不是 JSON", generation_payload()])
        result = generate_rubric(
            provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
        )
        self.assertEqual(result.responses[0], "不是 JSON")


class RejectEmptyInput(unittest.TestCase):
    def test_missing_inputs_are_rejected_before_calling_the_model(self):
        provider = CannedProvider([generation_payload()])
        for kwargs in (
            {"stem": "  ", "total_score": 10, "standard_text": STANDARD_TEXT},
            {"stem": STEM, "total_score": 0, "standard_text": STANDARD_TEXT},
            {"stem": STEM, "total_score": 10, "standard_text": " "},
        ):
            with self.assertRaises(ValueError):
                generate_rubric(provider, **kwargs)
        self.assertEqual(provider.calls, 0)


class ModelCannotChangeScoringBehaviour(unittest.TestCase):
    def test_rules_and_item_flags_from_the_model_are_dropped(self):
        data = json.loads(generation_payload())
        data["rules"] = {"ignore_length": True, "allow_inference": True}
        data["items"][0]["must_hit"] = True
        data["items"][0]["acceptable"] = ["随便怎么写都行"]
        provider = CannedProvider([json.dumps(data, ensure_ascii=False)])

        rubric = generate_rubric(
            provider, stem=STEM, total_score=10, standard_text=STANDARD_TEXT
        ).rubric

        self.assertEqual(rubric.rules, {})
        self.assertFalse(rubric.item("P1").must_hit)
        self.assertEqual(rubric.item("P1").acceptable, ())

    def test_shortfall_still_reported_when_another_field_is_broken(self):
        data = json.loads(generation_payload())
        del data["items"][2]["weight"]          # 结构错误 + 权重不足同时出现

        with self.assertRaises(RubricGenerationError) as ctx:
            generate_rubric(
                CannedProvider([json.dumps(data, ensure_ascii=False)]),
                stem=STEM,
                total_score=10,
                standard_text=STANDARD_TEXT,
            )

        message = str(ctx.exception)
        self.assertIn("3 分未分配", message)
        self.assertIn("缺少必填字段", message)


if __name__ == "__main__":
    unittest.main()
