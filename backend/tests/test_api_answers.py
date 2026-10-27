"""答卷导入接口的验收：预览不写库、入库换脱敏编号、重复被跳过。

用真 SQLite 库（迁移建表），因为"导入后能查回来""重复能被识别"这两条
本来就是对库的行为要求；把 Store 换成假的，测的就不是这些了。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    raise unittest.SkipTest("未安装 fastapi/httpx，见 backend/pyproject.toml")

from app.auth import AdminAuth
from app.main import create_app
from app.store import Store
from tests.support import database_url, upgrade_database

PASSWORD = "let-me-in-please"

CSV = "学号,题号,答案\n2024001,Q-01,客户端发送 SYN\n2024002,Q-01,服务器回复 SYN+ACK\n"


class ApiWithDatabase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.url = f"sqlite:///{Path(self.dir.name) / 'api.db'}"

        upgrade_database(self.url)
        env = database_url(self.url)
        env.__enter__()
        self.addCleanup(lambda: env.__exit__(None, None, None))

        self.auth = AdminAuth(PASSWORD, ttl_seconds=600)
        self.app = create_app(auth=self.auth)
        self.client = TestClient(self.app)
        self.client.post("/api/auth/login", json={"password": PASSWORD})

        # 先存一份标准，题目才会存在（导入要求题号已存在）
        self.store = Store.from_url(self.url)
        self.addCleanup(self.store.dispose)
        self._save_rubric()

    def _save_rubric(self):
        from app.core.rubric import from_dict
        from app.pipeline import evaluate
        from app.providers.fake import CannedProvider
        from tests.fixtures import ANSWER, RUBRIC_DATA, payload_text

        rubric = from_dict(RUBRIC_DATA)
        evaluation = evaluate(rubric, ANSWER, CannedProvider([payload_text()]), samples=1)
        self.store.save_evaluation(rubric=rubric, answer_text=ANSWER, evaluation=evaluation)

    def preview(self, text=CSV, **extra):
        return self.client.post(
            "/api/answers/preview", json={"source": "csv", "text": text, **extra}
        )

    def do_import(self, text=CSV, **extra):
        return self.client.post(
            "/api/answers/import", json={"source": "csv", "text": text, **extra}
        )


class Preview(ApiWithDatabase):
    def test_preview_reports_rows_questions_and_no_student_id(self):
        response = self.preview()

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["total_rows"], 2)
        self.assertEqual(body["question_codes"], ["Q-01"])
        self.assertEqual([row["line"] for row in body["rows"]], [2, 3])
        self.assertEqual(body["rows_with_student_id"], 2)
        self.assertTrue(body["questions"][0]["exists"])
        # 学号一个字符都不许出现在响应里
        self.assertNotIn("2024001", response.text)

    def test_preview_does_not_write_anything(self):
        before = self.store.count_answers("Q-01")
        self.preview()
        self.assertEqual(self.store.count_answers("Q-01"), before)

    def test_preview_marks_rows_already_in_the_database(self):
        self.do_import()

        body = self.preview().json()

        self.assertEqual([row["duplicate"] for row in body["rows"]], [True, True])

    def test_preview_reports_problem_rows_with_line_numbers(self):
        text = "学号,题号,答案\n2024001,Q-01,\n2024002,Q-01,正常\n"

        body = self.preview(text).json()

        self.assertEqual(len(body["problems"]), 1)
        self.assertEqual(body["problems"][0]["line"], 2)
        self.assertIn("答案为空", body["problems"][0]["reason"])

    def test_unknown_question_is_rejected_at_preview_too(self):
        """预览与入库必须给同一个结论，否则会出现"预览说能导、入库 404"。"""
        text = "题号,答案\nQ-99,随便答点\n"

        body = self.preview(text).json()

        self.assertFalse(body["ok"])
        self.assertIn("Q-99", body["error"])
        self.assertEqual(body["questions"], [{"code": "Q-99", "exists": False}])

    def test_garbage_input_returns_ok_false_with_a_reason(self):
        body = self.preview("只有一个字段").json()

        self.assertFalse(body["ok"])
        self.assertIn("2 列", body["error"])


class Import(ApiWithDatabase):
    def test_import_assigns_pseudo_ids_and_skips_duplicates(self):
        first = self.do_import()
        second = self.do_import()

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["imported"], 2)
        self.assertTrue(all(pid.startswith("S-") for pid in first.json()["pseudo_ids"]))
        self.assertEqual(second.json()["imported"], 0)
        self.assertEqual(second.json()["skipped_duplicates"], 2)
        self.assertNotIn("2024001", first.text)

    def test_paste_requires_question_code(self):
        response = self.client.post(
            "/api/answers/preview", json={"source": "paste", "text": "一段答案"}
        )
        self.assertFalse(response.json()["ok"])

    def test_paste_single_answer_is_imported_under_that_question(self):
        body = self.client.post(
            "/api/answers/import",
            json={"source": "paste", "text": "一段完整的答案，带逗号也可以", "question_code": "Q-01"},
        ).json()

        self.assertEqual(body["imported"], 1)

    def test_bad_rows_do_not_block_good_rows(self):
        text = "学号,题号,答案\n2024001,Q-01,\n2024002,Q-01,正常答案\n"

        body = self.do_import(text).json()

        self.assertEqual(body["imported"], 1)
        self.assertEqual(len(body["problems"]), 1)

    def test_importing_an_unknown_question_is_a_404(self):
        response = self.do_import("题号,答案\nQ-99,随便\n")

        self.assertEqual(response.status_code, 404, response.text)
        self.assertIn("Q-99", response.json()["detail"])

    def test_answers_can_be_listed_after_import(self):
        self.do_import()

        body = self.client.get("/api/answers", params={"question_code": "Q-01"}).json()

        self.assertEqual(body["total"], 3)      # setUp 里那份 + 导入的两份
        self.assertEqual(len(body["items"]), 3)


class DuplicateSemantics(ApiWithDatabase):
    def test_same_text_under_two_questions_is_not_a_duplicate(self):
        # Q-99 不存在，所以只验证"同内容不同题号"这一条：先建第二道题
        self._save_rubric_for("Q-02")

        body = self.preview(
            "题号,答案\nQ-01,同一段话\nQ-02,同一段话\n"
        ).json()

        self.assertEqual([row["duplicate"] for row in body["rows"]], [False, False])

    def test_duplicate_inside_the_same_file_is_flagged(self):
        body = self.preview("题号,答案\nQ-01,同一段话\nQ-01,同一段话\n").json()

        self.assertEqual([row["duplicate"] for row in body["rows"]], [False, True])

    def test_duplicate_against_the_database_is_flagged(self):
        self.do_import("题号,答案\nQ-01,已经在库里\n")

        body = self.preview("题号,答案\nQ-01,已经在库里\n").json()

        self.assertTrue(body["rows"][0]["duplicate"])

    def _save_rubric_for(self, code):
        from app.core.rubric import from_dict
        from app.pipeline import evaluate
        from app.providers.fake import CannedProvider
        from tests.fixtures import ANSWER, RUBRIC_DATA, payload_text

        data = dict(RUBRIC_DATA)
        data["question_id"] = code
        rubric = from_dict(data)
        evaluation = evaluate(rubric, ANSWER, CannedProvider([payload_text()]), samples=1)
        self.store.save_evaluation(rubric=rubric, answer_text=ANSWER, evaluation=evaluation)


class InvalidRequestDoesNotEchoInput(ApiWithDatabase):
    def test_422_keeps_the_submitted_text_out_of_the_response(self):
        response = self.client.post(
            "/api/answers/preview",
            json={"source": "csv", "text": {"学号": "2024001"}},
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertNotIn("2024001", response.text)
        self.assertIn("text", response.json()["detail"])


class DatabaseMissing(unittest.TestCase):
    def setUp(self):
        self.auth = AdminAuth(PASSWORD, ttl_seconds=600)
        env = mock.patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.app = create_app(auth=self.auth)
        self.client = TestClient(self.app)
        self.client.post("/api/auth/login", json={"password": PASSWORD})

    def test_business_routes_answer_503_with_a_usable_hint(self):
        response = self.client.post(
            "/api/answers/preview", json={"source": "csv", "text": CSV}
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("DATABASE_URL", response.json()["detail"])

    def test_auth_still_works_without_a_database(self):
        self.assertEqual(self.client.get("/api/auth/session").status_code, 200)


class RequiresLogin(ApiWithDatabase):
    def test_preview_and_import_are_protected(self):
        client = TestClient(self.app)

        self.assertEqual(
            client.post("/api/answers/preview", json={"source": "csv", "text": CSV}).status_code,
            401,
        )
        self.assertEqual(
            client.post("/api/answers/import", json={"source": "csv", "text": CSV}).status_code,
            401,
        )
        self.assertEqual(client.get("/api/answers", params={"question_code": "Q-01"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
