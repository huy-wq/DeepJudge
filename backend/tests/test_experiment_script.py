"""实验脚本入口：check 不调模型、无效用例退出码非 0、run 必须显式 --live。"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import experiment

from tests.fixtures import RUBRIC_DATA

CASES = {
    "question_code": "Q-01",
    "cases": [
        {
            "id": "perfect",
            "correct": True,
            "answer": "客户端发 SYN，服务器回 SYN+ACK，客户端回 ACK。",
            "expected": {"total": 10, "items": {"P1": 1, "P2": 1, "P3": 1}},
        }
    ],
}


class ExperimentScript(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.rubric = self.root / "rubric.json"
        self.cases = self.root / "cases.json"
        self.rubric.write_text(json.dumps(RUBRIC_DATA, ensure_ascii=False), encoding="utf-8")
        self.cases.write_text(json.dumps(CASES, ensure_ascii=False), encoding="utf-8")
        patcher = mock.patch("scripts.experiment.load_project_env", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_script(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = experiment.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_check_validates_without_calling_a_model(self):
        code, out, err = self.run_script(
            "check", "--rubric", str(self.rubric), "--cases", str(self.cases)
        )

        self.assertEqual(code, 0, err)
        self.assertIn("检查通过", out)
        self.assertIn("没有调用模型", out)

    def test_check_rejects_a_set_without_a_correct_case(self):
        data = json.loads(json.dumps(CASES))
        data["cases"][0]["correct"] = False
        self.cases.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        code, out, err = self.run_script(
            "check", "--rubric", str(self.rubric), "--cases", str(self.cases)
        )

        self.assertEqual(code, 2)
        self.assertIn("NO_CORRECT_CASE", err)

    def test_run_without_live_is_refused(self):
        code, out, err = self.run_script(
            "run", "--rubric", str(self.rubric), "--cases", str(self.cases)
        )

        self.assertEqual(code, 2)
        self.assertIn("--live", err)


if __name__ == "__main__":
    unittest.main()
