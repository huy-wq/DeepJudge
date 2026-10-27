"""生成标准脚本的入口行为：退出码、三视图输出、保存文件。

脚本本身很薄，所以只测"用户能看见的部分"：打印了什么、退出码是几、文件写没写。
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from app.providers.base import ProviderError
from scripts import gen_rubric

from tests.fixtures import RUBRIC_DATA

STEM = "简述 TCP 三次握手的过程，并说明其目的。"
STANDARD_TEXT = "三个要点：客户端发 SYN 给 3 分；服务器回 SYN+ACK 给 4 分；客户端回 ACK 给 3 分。"


def generation_payload(**overrides) -> dict:
    payload = {
        "summary": "三个采分点，权重 3/4/3。",
        "items": json.loads(json.dumps(RUBRIC_DATA["items"])),
        "deductions": json.loads(json.dumps(RUBRIC_DATA["deductions"])),
    }
    payload.update(overrides)
    return payload


class GenerationScript(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.response = self.root / "response.json"
        self.response.write_text(
            json.dumps(generation_payload(), ensure_ascii=False), encoding="utf-8"
        )
        # 不让本机 .env 参与：这些用例测的是"给了什么就用什么"
        patcher = mock.patch("scripts.gen_rubric.load_project_env", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_script(self, *extra):
        argv = [
            "--stem", STEM,
            "--total", "10",
            "--standard", STANDARD_TEXT,
            "--response", str(self.response),
            *extra,
        ]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gen_rubric.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_prints_three_views(self):
        code, out, err = self.run_script()

        self.assertEqual(code, 0, err)
        self.assertIn("【摘要】", out)
        self.assertIn("【预览表】", out)
        self.assertIn("【校验清单】", out)
        self.assertIn("P1", out)
        self.assertIn("没有发现问题", out)

    def test_saves_a_rubric_that_grading_can_consume(self):
        target = self.root / "out" / "rubric.json"

        code, out, err = self.run_script("--save-rubric", str(target))

        self.assertEqual(code, 0, err)
        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved["total_score"], 10)
        self.assertEqual([item["id"] for item in saved["items"]], ["P1", "P2", "P3"])

    def test_unusable_model_output_exits_with_generation_error(self):
        self.response.write_text("抱歉，我无法完成该任务。", encoding="utf-8")

        code, out, err = self.run_script()

        self.assertEqual(code, 3)
        self.assertIn("生成失败", err)
        self.assertEqual(out, "")

    def test_zero_total_is_rejected_by_the_parser(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            gen_rubric.main(
                [
                    "--stem", STEM,
                    "--total", "0",
                    "--standard", STANDARD_TEXT,
                    "--response", str(self.response),
                ]
            )
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("--total", err.getvalue())

    def test_unreadable_standard_file_exits_with_input_error(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gen_rubric.main(
                [
                    "--stem", STEM,
                    "--total", "10",
                    "--standard-file", str(self.root / "missing.txt"),
                    "--response", str(self.response),
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("读不到标准原文", err.getvalue())
        self.assertEqual(out.getvalue(), "")

    def test_provider_failure_during_generation_exits_with_provider_error(self):
        class Boom:
            def complete(self, messages, *, sample_index=0):
                raise ProviderError("RATE_LIMIT", "请求过于频繁", retryable=True)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(gen_rubric, "CannedProvider", lambda _: Boom()):
            with redirect_stdout(out), redirect_stderr(err):
                code = gen_rubric.main(
                    [
                        "--stem", STEM,
                        "--total", "10",
                        "--standard", STANDARD_TEXT,
                        "--response", str(self.response),
                    ]
                )

        self.assertEqual(code, 4)
        self.assertIn("模型调用失败", err.getvalue())
        self.assertIn("可重试", err.getvalue())
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
