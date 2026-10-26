import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from app.cli import main
from tests.fixtures import ANSWER, RUBRIC_DATA
from tests.fixtures import SAMPLE_PAYLOAD


class CliRunsOneGrading(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.rubric_path = self.root / "rubric.json"
        self.answer_path = self.root / "answer.txt"
        self.response_path = self.root / "response.json"
        self.rubric_path.write_text(json.dumps(RUBRIC_DATA, ensure_ascii=False), encoding="utf-8")
        self.answer_path.write_text(ANSWER, encoding="utf-8")
        self.response_path.write_text(
            json.dumps(SAMPLE_PAYLOAD, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(
                [
                    "--rubric", str(self.rubric_path),
                    "--answer", str(self.answer_path),
                    "--response", str(self.response_path),
                    *extra,
                ]
            )
        return code, out.getvalue(), err.getvalue()

    def test_prints_score_and_per_item_breakdown(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual(report["score"], 8.0)
        self.assertEqual([i["id"] for i in report["items"]], ["P1", "P2", "P3"])
        self.assertEqual(report["items"][1]["level"], 0.5)
        self.assertTrue(report["items"][0]["evidence"])

    def test_invalid_rubric_exits_nonzero_with_all_problems(self):
        broken = json.loads(json.dumps(RUBRIC_DATA))
        broken["total_score"] = 11
        self.rubric_path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        code, out, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("权重合计", err)
        self.assertEqual(out, "")




class CliWithMultipleSamples(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.rubric_path = root / "rubric.json"
        self.answer_path = root / "answer.txt"
        self.response_path = root / "model.json"
        self.rubric_path.write_text(json.dumps(RUBRIC_DATA, ensure_ascii=False), encoding="utf-8")
        self.answer_path.write_text(ANSWER, encoding="utf-8")
        self.response_path.write_text(
            json.dumps(SAMPLE_PAYLOAD, ensure_ascii=False), encoding="utf-8"
        )

    def tearDown(self):
        self.dir.cleanup()

    def test_reports_aggregate_and_route(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main([
                "--rubric", str(self.rubric_path),
                "--answer", str(self.answer_path),
                "--response", str(self.response_path),
                "--samples", "3",
            ])

        self.assertEqual(code, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["samples"], 3)
        self.assertEqual(len(report["items"][0]["levels"]), 3)
        self.assertEqual(report["score"], 8.0)
        self.assertEqual(report["confidence"], 1.0)
        self.assertEqual(report["route"], "auto")


class CliSavesToDatabase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        self.rubric_path = root / "rubric.json"
        self.answer_path = root / "answer.txt"
        self.response_path = root / "response.json"
        self.rubric_path.write_text(json.dumps(RUBRIC_DATA, ensure_ascii=False), encoding="utf-8")
        self.answer_path.write_text(ANSWER, encoding="utf-8")
        self.response_path.write_text(
            json.dumps(SAMPLE_PAYLOAD, ensure_ascii=False), encoding="utf-8"
        )
        # 不让本机 .env 参与：这个类测的是"给了什么就用什么"
        patcher = mock.patch("app.cli.load_project_env", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(
                [
                    "--rubric", str(self.rubric_path),
                    "--answer", str(self.answer_path),
                    "--response", str(self.response_path),
                    *extra,
                ]
            )
        return code, out.getvalue(), err.getvalue()

    def test_prints_ids_that_can_be_read_back(self):
        try:
            from app.store import Store
        except ImportError:  # pragma: no cover
            self.skipTest("未安装 sqlalchemy")

        from tests.support import create_schema

        db_path = Path(self.dir.name) / "save.db"
        url = f"sqlite+pysqlite:///{db_path}"
        store = Store.from_url(url)
        create_schema(store)
        store.dispose()
        self.addCleanup(lambda: os.environ.pop("DATABASE_URL", None))

        code, out, err = self.run_cli("--save", "--database-url", url, "--pseudo-id", "S-001")
        self.assertEqual(code, 0, err)

        stored = json.loads(out)["stored"]
        store = Store.from_url(url)
        self.addCleanup(store.dispose)
        loaded = store.load_evaluation(answer_id=stored["answer_id"])
        self.assertEqual(loaded.answer_text, ANSWER)
        self.assertEqual(len(loaded.run_ids), 1)

    def test_missing_database_url_exits_nonzero(self):
        self.addCleanup(lambda: os.environ.pop("DATABASE_URL", None))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            code, out, err = self.run_cli("--save")
        self.assertEqual(code, 5)
        self.assertIn("DATABASE_URL", err)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
