"""标准接口的验收：生成 → 保存 → 列表/打开 → 行内编辑 → 迭代。

用真 SQLite 文件库 + 脚本化假 provider：生成请求返回生成样本，评分请求返回评分样本，
从 HTTP 这条缝断言教师实际能做的事，以及"改了标准不会动到已经建好的任务"。
"""

import json
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("未安装 fastapi/httpx，见 backend/pyproject.toml")

from app.auth import AdminAuth
from app.main import create_app
from app.providers.base import ProviderError
from app.store import Store
from tests.fixtures import RUBRIC_DATA, generation_payload, payload_text, rubric_variant
from tests.support import create_schema, database_url

PASSWORD = "let-me-in-please"
STEM = RUBRIC_DATA["stem"]
STANDARD_TEXT = (
    "三个要点：客户端发 SYN 与初始序列号给 3 分；服务器回 SYN+ACK 给 4 分；"
    "客户端再回 ACK 给 3 分。"
)


class ScriptedProvider:
    """按本轮任务的提示词类型返回不同样本：生成 / 评分。"""

    model_name = ""

    def __init__(self, seen: list[list[dict]], *, fail: bool = False) -> None:
        self.seen = seen
        self.fail = fail

    def complete(self, messages: list[dict], *, sample_index: int = 0) -> str:
        self.seen.append([dict(message) for message in messages])
        if self.fail:
            raise ProviderError("MISSING_API_KEY", "未配置 DEEPSEEK_API_KEY", retryable=False)
        if "命题教师助理" in messages[0]["content"]:
            return generation_payload()
        return payload_text()


class RubricApiTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.url = f"sqlite:///{Path(self.dir.name) / 'rubrics-api.db'}"
        self.store = Store.from_url(self.url)
        create_schema(self.store)
        self.addCleanup(self.store.dispose)

        self.seen: list[list[dict]] = []
        env = database_url(self.url)
        env.__enter__()
        self.addCleanup(lambda: env.__exit__(None, None, None))
        self.app = create_app(
            auth=AdminAuth(PASSWORD, ttl_seconds=600),
            provider_factory=lambda: ScriptedProvider(self.seen),
        )
        self._client_cm = TestClient(self.app)
        self.client = self._client_cm.__enter__()
        self.addCleanup(lambda: self._client_cm.__exit__(None, None, None))
        self.client.post("/api/auth/login", json={"password": PASSWORD})

    def tearDown(self):
        runner = getattr(self.app.state, "runner", None)
        if runner is not None:
            runner.wait_all(timeout=15)

    def save(self, rubric=None, **extra):
        payload = {"rubric": rubric or RUBRIC_DATA, "source_text": STANDARD_TEXT, **extra}
        return self.client.post("/api/rubrics", json=payload)

    def generate(self, **overrides):
        payload = {
            "stem": STEM,
            "total_score": 10,
            "standard_text": STANDARD_TEXT,
            "reference_answer": "SYN / SYN+ACK / ACK。",
            **overrides,
        }
        return self.client.post("/api/rubrics/generate", json=payload)


class SaveAndReopen(RubricApiTestCase):
    def test_saved_rubric_appears_in_the_list_and_can_be_reopened(self):
        created = self.save(source_text=STANDARD_TEXT)

        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertEqual(body["question_code"], "Q-01")
        self.assertEqual(body["version"], 1)
        self.assertFalse(body["reused"])

        listing = self.client.get("/api/rubrics").json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["rubric_id"], body["rubric_id"])

        reopened = self.client.get(f"/api/rubrics/{body['rubric_id']}").json()
        self.assertEqual(reopened["rubric"]["items"][0]["desc"], "客户端发送 SYN 及初始序列号")
        self.assertEqual(reopened["source_text"], STANDARD_TEXT)
        self.assertEqual(reopened["issues"], [])

    def test_identical_content_does_not_create_an_empty_version(self):
        first = self.save().json()
        second = self.save(feedback="重新点了一次保存").json()

        self.assertTrue(second["reused"])
        self.assertEqual(second["rubric_id"], first["rubric_id"])
        self.assertEqual(second["version"], 1)

    def test_first_save_without_the_teacher_words_is_rejected(self):
        response = self.client.post(
            "/api/rubrics", json={"rubric": RUBRIC_DATA, "source_text": "  "}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["issues"][0]["code"], "SOURCE_TEXT_REQUIRED")

    def test_unknown_rubric_is_404(self):
        self.assertEqual(self.client.get("/api/rubrics/9999").status_code, 404)


class Lineage(RubricApiTestCase):
    def test_editing_records_which_version_it_came_from(self):
        first = self.save().json()

        second = self.client.put(
            f"/api/rubrics/{first['rubric_id']}",
            json={"rubric": rubric_variant(_items={"P1": {"desc": "改过的描述"}})},
        ).json()

        self.assertEqual(second["parent_rubric_id"], first["rubric_id"])
        reopened = self.client.get(f"/api/rubrics/{second['rubric_id']}").json()
        self.assertEqual(reopened["parent_rubric_id"], first["rubric_id"])


class InlineEdit(RubricApiTestCase):
    def test_editing_one_row_saves_a_new_version_and_keeps_the_old_one(self):
        first = self.save().json()

        response = self.client.put(
            f"/api/rubrics/{first['rubric_id']}",
            json={
                "rubric": rubric_variant(_items={"P1": {"desc": "客户端发送 SYN，并给出初始序列号"}}),
                "feedback": "P1 描述写具体",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        second = response.json()
        self.assertEqual(second["version"], 2)
        self.assertNotEqual(second["rubric_id"], first["rubric_id"])
        self.assertEqual(
            self.client.get(f"/api/rubrics/{first['rubric_id']}").json()["rubric"]["items"][0][
                "desc"
            ],
            "客户端发送 SYN 及初始序列号",
        )
        self.assertEqual(
            self.client.get(f"/api/rubrics/{second['rubric_id']}").json()["rubric"]["items"][0][
                "desc"
            ],
            "客户端发送 SYN，并给出初始序列号",
        )

    def test_editing_weights_and_level_scores_can_be_saved(self):
        first = self.save().json()
        rubric = rubric_variant()
        rubric["items"][0]["weight"] = 2
        rubric["items"][0]["levels"] = [
            {"level": 1.0, "score": 2.0, "criterion": "明确 SYN=1、seq=x"},
            {"level": 0.5, "score": 1.0, "criterion": "只提到发送连接请求"},
            {"level": 0.0, "score": 0.0, "criterion": "未提及"},
        ]
        rubric["items"][1]["weight"] = 5
        rubric["items"][1]["levels"] = [
            {"level": 1.0, "score": 5.0, "criterion": "SYN、ACK、seq、ack 均正确"},
            {"level": 0.5, "score": 2.5, "criterion": "只说服务器同意，缺字段"},
            {"level": 0.0, "score": 0.0, "criterion": "未提及"},
        ]

        response = self.client.put(
            f"/api/rubrics/{first['rubric_id']}", json={"rubric": rubric}
        )

        self.assertEqual(response.status_code, 200, response.text)
        reopened = self.client.get(f"/api/rubrics/{response.json()['rubric_id']}").json()
        self.assertEqual(reopened["rubric"]["items"][0]["weight"], 2.0)
        self.assertEqual(reopened["rubric"]["items"][1]["levels"][1]["score"], 2.5)

    def test_editing_keeps_the_teacher_words_when_not_resent(self):
        first = self.save(source_text=STANDARD_TEXT).json()
        second = self.client.put(
            f"/api/rubrics/{first['rubric_id']}",
            json={"rubric": rubric_variant(_items={"P1": {"desc": "只改描述"}})},
        ).json()

        self.assertEqual(
            self.client.get(f"/api/rubrics/{second['rubric_id']}").json()["source_text"],
            STANDARD_TEXT,
        )

    def test_structural_error_is_rejected_and_points_at_the_item(self):
        first = self.save().json()

        response = self.client.put(
            f"/api/rubrics/{first['rubric_id']}",
            json={"rubric": rubric_variant(_items={"P1": {"weight": 4}})},
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "RUBRIC_INVALID")
        self.assertIn("WEIGHT_SUM", [issue["code"] for issue in body["issues"]])
        self.assertIn("超出", body["issues"][0]["message"])
        # 被拒绝的保存没有落下任何新版本
        self.assertEqual(self.client.get("/api/rubrics").json()["total"], 1)

    def test_out_of_range_level_score_names_the_item(self):
        first = self.save().json()
        rubric = rubric_variant()
        rubric["items"][2]["levels"][0]["score"] = 9.0

        response = self.client.put(
            f"/api/rubrics/{first['rubric_id']}", json={"rubric": rubric}
        )

        self.assertEqual(response.status_code, 400)
        issues = response.json()["issues"]
        self.assertIn("SCORE_OUT_OF_RANGE", [issue["code"] for issue in issues])
        self.assertTrue(any("P3" in issue["message"] for issue in issues))

    def test_question_code_cannot_be_changed_by_an_edit(self):
        first = self.save().json()

        response = self.client.put(
            f"/api/rubrics/{first['rubric_id']}",
            json={"rubric": {**RUBRIC_DATA, "question_id": "Q-02"}},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["issues"][0]["code"], "QUESTION_CHANGED")

    def test_all_structural_problems_are_reported_not_just_the_first(self):
        first = self.save().json()
        rubric = rubric_variant(_items={"P1": {"weight": 4}, "P2": {"weight": 4}})
        rubric["items"][2]["levels"][0]["score"] = 9.0

        response = self.client.put(
            f"/api/rubrics/{first['rubric_id']}", json={"rubric": rubric}
        )

        codes = {issue["code"] for issue in response.json()["issues"]}
        self.assertIn("WEIGHT_SUM", codes)
        self.assertIn("SCORE_OUT_OF_RANGE", codes)


class Generate(RubricApiTestCase):
    def test_generate_returns_the_three_views_and_the_draft(self):
        response = self.generate()

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ("rubric", "summary", "preview", "checklist", "attempts"):
            self.assertIn(key, body)
        self.assertEqual([row["id"] for row in body["preview"]], ["P1", "P2", "P3"])
        self.assertEqual(body["rubric"]["total_score"], 10)

    def test_generate_rejects_a_missing_stem(self):
        response = self.generate(stem="")

        self.assertEqual(response.status_code, 400)
        self.assertIn("题干", response.json()["detail"])

    def test_generate_reports_a_provider_failure_with_its_code(self):
        self.app.state.provider_factory = lambda: ScriptedProvider(self.seen, fail=True)

        response = self.generate()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "MISSING_API_KEY")

    def test_generation_failure_after_retry_is_422_with_issues(self):
        # 模型两次都给出非法等级 → 生成失败，响应里要带上问题清单
        bad = json.dumps(
            {
                "summary": "坏样本",
                "items": [
                    {"id": "P1", "desc": "x", "weight": 10,
                     "levels": [{"level": 1.0, "score": 10, "criterion": "a"}]},
                ],
                "deductions": [],
            },
            ensure_ascii=False,
        )
        self.app.state.provider_factory = lambda: type(
            "Bad",
            (),
            {"model_name": "", "complete": lambda self, messages, sample_index=0: bad},
        )()

        response = self.generate()

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "RUBRIC_GENERATION_FAILED")
        self.assertTrue(response.json()["issues"])


class Iterate(RubricApiTestCase):
    def test_iteration_carries_the_previous_version_and_the_feedback(self):
        first = self.save().json()

        response = self.client.post(
            f"/api/rubrics/{first['rubric_id']}/iterate",
            json={"feedback": "P3 拆得太粗，拆成两个采分点"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["based_on"]["version"], 1)
        self.assertEqual(body["based_on"]["rubric_id"], first["rubric_id"])
        # 服务端 diff：假 provider 原样返回上一版，所以"没改任何东西"
        self.assertEqual(body["changes"], [])
        # 服务端拥有题号/题干/满分，模型改不动
        self.assertEqual(body["rubric"]["question_id"], "Q-01")
        self.assertEqual(body["rubric"]["total_score"], 10)

        prompt = self.seen[-1]
        self.assertIn("P3 拆得太粗", prompt[1]["content"])
        self.assertIn("<previous>", prompt[1]["content"])
        self.assertIn("服务器回复 SYN+ACK", prompt[1]["content"])

    def test_iteration_requires_feedback(self):
        first = self.save().json()

        response = self.client.post(
            f"/api/rubrics/{first['rubric_id']}/iterate", json={"feedback": "  "}
        )

        self.assertEqual(response.status_code, 400)

    def test_iterating_an_unknown_rubric_is_404(self):
        response = self.client.post("/api/rubrics/9999/iterate", json={"feedback": "改"})
        self.assertEqual(response.status_code, 404)


class TaskSnapshotAcrossEdits(RubricApiTestCase):
    def test_a_task_keeps_the_rubric_version_it_was_created_with(self):
        first = self.save(source_text=STANDARD_TEXT).json()
        outcome = self.store.import_answers([("Q-01", "一份答卷")], source="csv")
        answer_id = outcome.imported[0].answer_id

        task = self.client.post(
            "/api/tasks",
            json={"question_code": "Q-01", "answer_ids": [answer_id], "samples": 1},
        ).json()
        self.assertEqual(task["rubric_id"], first["rubric_id"])

        edited = self.client.put(
            f"/api/rubrics/{first['rubric_id']}",
            json={"rubric": rubric_variant(_items={"P1": {"desc": "后来改的描述"}})},
        ).json()
        self.assertEqual(edited["version"], 2)

        # 任务仍指向旧版本；旧版本内容也没被改写
        task_after = self.client.get(f"/api/tasks/{task['task_id']}").json()
        self.assertEqual(task_after["rubric_id"], first["rubric_id"])
        reopened = self.client.get(f"/api/rubrics/{first['rubric_id']}").json()
        self.assertEqual(reopened["rubric"]["items"][0]["desc"], "客户端发送 SYN 及初始序列号")
        latest = self.client.get("/api/rubrics").json()["items"][0]
        self.assertEqual(latest["version"], 2)


if __name__ == "__main__":
    unittest.main()
