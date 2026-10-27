"""接口层共享的请求/响应模型。

只放 HTTP 边界的形状，不放业务规则——口令怎么校验在 `app/auth.py`，
答卷怎么解析在 `app/importing.py`。模型放在这里而不是各省一个路由文件里，
是为了让"接口长什么样"能一眼看完。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 认证
class LoginRequest(BaseModel):
    password: str = Field(default="", description="管理端口令")


class SessionInfo(BaseModel):
    authenticated: bool
    expires_at: float = Field(description="会话到期时间（Unix 秒）")


# ---------------------------------------------------------------- 答卷导入
class ImportRequest(BaseModel):
    source: str = Field(default="csv", description="csv 或 paste")
    text: str = Field(description="CSV 文本，或单份答案原文")
    question_code: str | None = Field(
        default=None, description="指定题号后，CSV 可省略题号列；单份粘贴必填"
    )


class ImportConfirm(ImportRequest):
    skip_duplicates: bool = Field(
        default=True, description="同题号 + 相同内容视为重复，默认跳过"
    )
