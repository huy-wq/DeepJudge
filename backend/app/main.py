"""应用装配。

用工厂函数而不是模块级 `app`：口令缺失时应该在**启动**那一刻就炸，
而不是在 import 时炸（import 期抛错会让测试收集、脚本引用一起遭殃），
也不该悄悄跑起来一个没有口令的管理端。

启动方式（需要 `--factory`）：

    uvicorn app.main:create_app --factory --port 8000
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI

from .api import errors
from .api import register as register_auth
from .api.answers import register as register_answers
from .api.tasks import register as register_tasks
from .auth import SESSION_TTL_SECONDS, AdminAuth
from .providers.base import GradingProvider, ProviderError
from .providers.deepseek import DeepSeekConfig, DeepSeekProvider
from .store import Store
from .tasks import TaskRunner, concurrency_from_env

DEFAULT_SESSION_HOURS = SESSION_TTL_SECONDS // 3600


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def session_hours_from_env() -> int:
    raw = (os.environ.get("ADMIN_SESSION_HOURS") or "").strip()
    if not raw:
        return DEFAULT_SESSION_HOURS
    try:
        hours = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"ADMIN_SESSION_HOURS 必须是整数小时：{raw!r}") from exc
    if hours <= 0:
        raise RuntimeError("ADMIN_SESSION_HOURS 必须大于 0")
    return hours


def create_app(
    *,
    auth: AdminAuth | None = None,
    provider_factory: Callable[[], GradingProvider] | None = None,
) -> FastAPI:
    """装配 FastAPI 应用。

    传入 `auth` 时完全按传入的来（测试用假时钟）；
    否则从环境变量取 `ADMIN_PASSWORD`，没有就直接报错。
    `provider_factory` 让测试塞假 provider；不传就用 DeepSeek。
    """
    if auth is None:
        password = (os.environ.get("ADMIN_PASSWORD") or "").strip()
        if not password:
            # 启动期失败优于运行期放行：没有口令的管理端等于没有门。
            raise RuntimeError(
                "ADMIN_PASSWORD 未设置：管理端不能在没有口令的情况下运行。"
                "请在 .env 或环境变量里配置。"
            )
        auth = AdminAuth(password, ttl_seconds=session_hours_from_env() * 3600)

    store = _store_from_env()
    factory = provider_factory or _default_provider

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.store is not None:
            # 上次进程死在跑任务上时，别让任务永远显示"进行中"
            app.state.store.mark_interrupted_tasks()
        yield
        if app.state.store is not None:
            app.state.store.dispose()

    app = FastAPI(title="DeepJudge", version="0.1.0", lifespan=lifespan)
    app.state.auth = auth
    app.state.store = store
    app.state.runner = (
        TaskRunner(store, factory, concurrency=concurrency_from_env()) if store else None
    )

    errors.register(app)
    register_auth(app, auth, secure_cookie=_env_flag("COOKIE_SECURE"))
    register_answers(app)
    register_tasks(app)
    return app


def _default_provider() -> GradingProvider:
    """默认用 DeepSeek。密钥缺失时抛 ProviderError，由任务记成单份失败而不是崩掉整批。"""
    if not (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
        raise ProviderError("MISSING_API_KEY", "未配置 DEEPSEEK_API_KEY", retryable=False)
    return DeepSeekProvider(DeepSeekConfig.from_env())


def _store_from_env() -> Store | None:
    """没有 DATABASE_URL 就让业务接口返回 503，而不是让整个服务起不来。

    认证与前端骨架不该被"这台机器没装 MySQL"卡住（本机就没有）。
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return None
    return Store.from_url(url)
