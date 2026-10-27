"""认证接口的请求与响应模型。

只放 HTTP 边界的形状，不放业务规则——口令怎么校验在 `app/auth.py`。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str = Field(default="", description="管理端口令")


class SessionInfo(BaseModel):
    authenticated: bool
    expires_at: float = Field(description="会话到期时间（Unix 秒）")
