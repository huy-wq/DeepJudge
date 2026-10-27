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

from fastapi import FastAPI

from .api import errors
from .api import register as register_auth
from .api.answers import register as register_answers
from .auth import SESSION_TTL_SECONDS, AdminAuth
from .store import Store

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


def create_app(*, auth: AdminAuth | None = None) -> FastAPI:
    """装配 FastAPI 应用。

    传入 `auth` 时完全按传入的来（测试用假时钟）；
    否则从环境变量取 `ADMIN_PASSWORD`，没有就直接报错。
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if app.state.store is not None:
            app.state.store.dispose()

    app = FastAPI(title="DeepJudge", version="0.1.0", lifespan=lifespan)
    app.state.auth = auth
    app.state.store = store

    errors.register(app)
    register_auth(app, auth, secure_cookie=_env_flag("COOKIE_SECURE"))
    register_answers(app)
    return app


def _store_from_env() -> Store | None:
    """没有 DATABASE_URL 就让业务接口返回 503，而不是让整个服务起不来。

    认证与前端骨架不该被"这台机器没装 MySQL"卡住（本机就没有）。
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return None
    return Store.from_url(url)
