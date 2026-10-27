"""认证相关路由：登录、退出、会话查询，外加一个公开的健康检查。

路由只做三件事：读请求 → 调 `AdminAuth` → 设/清 cookie。
判断逻辑一行都不在这里，所以这一层薄到几乎不用测。
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

from ..auth import SESSION_COOKIE, AdminAuth, AuthError, Session
from .deps import get_auth, require_admin
from .schemas import LoginRequest, SessionInfo


def _set_session_cookie(
    response: Response, auth: AdminAuth, sid: str, *, secure: bool
) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=sid,
        max_age=auth.ttl_seconds,
        httponly=True,   # JS 读不到，降低 XSS 窃取风险
        samesite="lax",  # 跨站表单提交不带上，缓解 CSRF
        secure=secure,   # 公网必须 HTTPS，否则口令与 cookie 明文传输
        path="/",
    )


def register(app: FastAPI, auth: AdminAuth, *, secure_cookie: bool = False) -> None:
    """把认证路由挂到应用上。"""

    @app.post("/api/auth/login", status_code=status.HTTP_204_NO_CONTENT)
    def login(payload: LoginRequest, request: Request, response: Response) -> None:
        del request  # 登录不需要依赖，占位保持签名一致
        try:
            session = auth.login(payload.password)
        except AuthError as exc:
            # 口令错、口令为空、口令为 None 都给同一个响应：
            # 响应里不出现"用户不存在""口令长度不对"这类可被利用的差异。
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="口令错误",
            ) from exc
        _set_session_cookie(response, auth, session.sid, secure=secure_cookie)

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(request: Request, response: Response) -> None:
        # 幂等：没登录也能退出，避免前端在会话已过期时卡住
        auth.logout(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")

    @app.get("/api/auth/session", response_model=SessionInfo)
    def session_info(session: Session = Depends(require_admin)) -> SessionInfo:
        return SessionInfo(authenticated=True, expires_at=session.expires_at)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """公开接口：证明"受保护"指的是管理接口，不是整站都要登录。"""
        return {"status": "ok"}


__all__ = ["register", "get_auth", "require_admin"]
