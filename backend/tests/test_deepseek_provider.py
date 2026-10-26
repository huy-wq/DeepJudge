import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from app.cli import main
from app.providers.base import ProviderError
from app.providers.deepseek import DeepSeekConfig, DeepSeekProvider, error_for_status
from tests.fixtures import ANSWER, RUBRIC_DATA

OK_CONTENT = '{"items": []}'
OK_RESPONSE = json.dumps({"choices": [{"message": {"content": OK_CONTENT}}]})


class Transport:
    """记录请求、返回预设响应，不发网络。"""

    def __init__(self, response: str = OK_RESPONSE, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append(
            {"url": url, "headers": headers, "body": json.loads(payload), "timeout": timeout}
        )
        if self.error:
            raise self.error
        return self.response


def make_provider(transport, **config):
    params = {"api_key": "sk-test", "model": "deepseek-flash"}
    params.update(config)
    return DeepSeekProvider(DeepSeekConfig(**params), transport=transport)


class RequestShape(unittest.TestCase):
    def test_calls_chat_completions_with_bearer_token(self):
        transport = Transport()
        provider = make_provider(transport)

        content = provider.complete([{"role": "user", "content": "hi"}])

        call = transport.calls[0]
        self.assertEqual(call["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(content, OK_CONTENT)

    def test_body_asks_for_json_and_enables_thinking(self):
        transport = Transport()
        make_provider(transport).complete([{"role": "user", "content": "hi"}])

        body = transport.calls[0]["body"]
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertNotIn("temperature", body, "思考模式下该参数不生效，不应发送")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])

    def test_thinking_can_be_disabled(self):
        transport = Transport()
        make_provider(transport, thinking=False).complete([{"role": "user", "content": "hi"}])

        body = transport.calls[0]["body"]
        self.assertNotIn("thinking", body)
        self.assertNotIn("reasoning_effort", body)


class ResponseHandling(unittest.TestCase):
    def test_empty_content_is_a_retryable_error(self):
        empty = json.dumps({"choices": [{"message": {"content": "  "}}]})
        with self.assertRaises(ProviderError) as ctx:
            make_provider(Transport(empty)).complete([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "EMPTY_RESPONSE")
        self.assertTrue(ctx.exception.retryable)

    def test_malformed_response_is_retryable(self):
        with self.assertRaises(ProviderError) as ctx:
            make_provider(Transport("not json")).complete([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "BAD_RESPONSE")
        self.assertTrue(ctx.exception.retryable)

    def test_missing_choices_is_a_retryable_error(self):
        with self.assertRaises(ProviderError) as ctx:
            make_provider(Transport(json.dumps({"data": []}))).complete(
                [{"role": "user", "content": "hi"}]
            )
        self.assertEqual(ctx.exception.code, "BAD_RESPONSE")

    def test_status_mapping(self):
        self.assertTrue(error_for_status(429).retryable)
        self.assertTrue(error_for_status(500).retryable)
        self.assertTrue(error_for_status(503).retryable)
        self.assertFalse(error_for_status(401).retryable)
        self.assertFalse(error_for_status(402).retryable)
        self.assertFalse(error_for_status(400).retryable)


class ConfigFromEnv(unittest.TestCase):
    def test_missing_key_is_rejected_without_retry(self):
        with self.assertRaises(ProviderError) as ctx:
            DeepSeekConfig.from_env({})
        self.assertEqual(ctx.exception.code, "MISSING_API_KEY")
        self.assertFalse(ctx.exception.retryable)

    def test_reads_overrides(self):
        config = DeepSeekConfig.from_env(
            {
                "DEEPSEEK_API_KEY": "sk-x",
                "DEEPSEEK_MODEL": "deepseek-v4-pro",
                "DEEPSEEK_BASE_URL": "https://example.test",
                "DEEPSEEK_TIMEOUT": "30",
                "DEEPSEEK_MAX_TOKENS": "512",
                "DEEPSEEK_REASONING_EFFORT": "low",
            }
        )
        self.assertEqual(config.model, "deepseek-v4-pro")
        self.assertEqual(config.base_url, "https://example.test")
        self.assertEqual(config.timeout, 30.0)
        self.assertEqual(config.max_tokens, 512)
        self.assertEqual(config.reasoning_effort, "low")


class LiveModeWithoutKey(unittest.TestCase):
    def test_exits_with_four_and_explains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rubric.json").write_text(json.dumps(RUBRIC_DATA, ensure_ascii=False), encoding="utf-8")
            (root / "answer.txt").write_text(ANSWER, encoding="utf-8")

            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("DEEPSEEK_API_KEY", None)
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main([
                        "--rubric", str(root / "rubric.json"),
                        "--answer", str(root / "answer.txt"),
                        "--live",
                    ])

        self.assertEqual(code, 4)
        self.assertIn("DEEPSEEK_API_KEY", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
