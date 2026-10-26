"""DeepSeek Provider（OpenAI 兼容的 Chat Completions）。

刻意不依赖厂商 SDK：用标准库发一个 JSON 请求就够，少一个装不上的依赖。
请求体按官方文档组织——JSON 输出、思考模式、思考强度；思考模式下不传 temperature。
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .base import ProviderError

Transport = Callable[[str, dict, bytes, float], str]

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TOKENS = 2048


@dataclass
class DeepSeekConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    max_tokens: int = DEFAULT_MAX_TOKENS
    reasoning_effort: str | None = "high"
    thinking: bool = True

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "DeepSeekConfig":
        env = os.environ if environ is None else environ
        api_key = (env.get("DEEPSEEK_API_KEY") or "").strip()
        if not api_key:
            raise ProviderError(
                "MISSING_API_KEY", "未配置 DEEPSEEK_API_KEY", retryable=False
            )
        return cls(
            api_key=api_key,
            model=(env.get("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip(),
            base_url=(env.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).strip(),
            timeout=float(env.get("DEEPSEEK_TIMEOUT") or DEFAULT_TIMEOUT),
            max_tokens=int(env.get("DEEPSEEK_MAX_TOKENS") or DEFAULT_MAX_TOKENS),
            reasoning_effort=(env.get("DEEPSEEK_REASONING_EFFORT") or "high").strip(),
        )


def _http_post(url: str, headers: dict, payload: bytes, timeout: float) -> str:
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:  # 读取错误体失败不应掩盖状态码
            pass
        raise error_for_status(exc.code, body) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise ProviderError("NETWORK", f"网络错误：{exc}", retryable=True) from exc


def error_for_status(status: int, body: str = "") -> ProviderError:
    """把 HTTP 状态映射成可重试 / 不可重试的错误。"""
    detail = body.strip()[:300]
    if status in (401, 403):
        return ProviderError("AUTH", f"鉴权失败（{status}）：{detail}", retryable=False)
    if status == 402:
        return ProviderError("NO_BALANCE", f"余额不足：{detail}", retryable=False)
    if status == 429:
        return ProviderError("RATE_LIMIT", f"限流：{detail}", retryable=True)
    if 400 <= status < 500:
        return ProviderError("BAD_REQUEST", f"请求被拒（{status}）：{detail}", retryable=False)
    return ProviderError("SERVER", f"服务端错误（{status}）：{detail}", retryable=True)


class DeepSeekProvider:
    def __init__(self, config: DeepSeekConfig, transport: Transport | None = None) -> None:
        self.config = config
        self._transport = transport or _http_post

    def build_body(self, messages: list[dict]) -> dict:
        body: dict = {
            "model": self.config.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": self.config.max_tokens,
        }
        if self.config.thinking:
            body["thinking"] = {"type": "enabled"}
            if self.config.reasoning_effort:
                body["reasoning_effort"] = self.config.reasoning_effort
        # 思考模式下 temperature 不生效，官方文档也建议不要传，因此默认不带。
        return body

    def complete(self, messages: list[dict], *, sample_index: int = 0) -> str:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = json.dumps(self.build_body(messages), ensure_ascii=False).encode("utf-8")
        raw = self._transport(url, headers, payload, self.config.timeout)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "BAD_RESPONSE", f"响应不是合法 JSON：{exc}", retryable=True
            ) from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "BAD_RESPONSE", f"响应缺少 choices[0].message.content：{raw[:200]}",
                retryable=True,
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise ProviderError("EMPTY_RESPONSE", "模型返回空内容", retryable=True)
        return content
