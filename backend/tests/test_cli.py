import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from app.cli import main
from tests.fixtures import ANSWER, RUBRIC_DATA
from tests.test_pipeline import SAMPLE_PAYLOAD


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


if __name__ == "__main__":
    unittest.main()
