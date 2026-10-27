"""FastAPI 依赖：从 cookie 取出会话，判不过就 401。

依赖只回答一个问题："这次请求是不是已登录的管理员"。回答不了就抛，
不让路由自己写 if not logged_in。
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from ..auth import SESSION_COOKIE, AdminAuth, AuthError, Session


def get_auth(request: Request) -> AdminAuth:
    """从应用状态取认证器。测试可以塞一个带假时钟的实例进去。"""
    auth = getattr(request.app.state, "auth", None)
    if auth is None:  # pragma: no cover - 装配错了才会走到
        raise RuntimeError("应用未装配 AdminAuth")
    return auth


def require_admin(request: Request) -> Session:
    """受保护接口的依赖：没有有效会话就 401。"""
    auth = get_auth(request)
    try:
        return auth.verify(request.cookies.get(SESSION_COOKIE))
    except AuthError as exc:
        # 过期、伪造、已退出对客户端是同一件事：重新登录。
        # 具体原因写进服务端日志的时机留到有审计需求时，不在响应里区分。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或会话已失效",
            headers={"WWW-Authenticate": "Cookie"},
        ) from exc
