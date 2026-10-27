"""接口错误映射：把领域异常翻译成"人能读 + 状态码对"的响应。

集中在一处的好处是**前端只需要认一种错误形状**：`{detail}` 或 `{detail, issues}`。
散在各路由里 `try/except` 的话，迟早出现"这个接口返回 HTML 报错"那种事。
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from fastapi.exceptions import RequestValidationError

from ..store import QuestionNotFound


def register(app: FastAPI) -> None:
    @app.exception_handler(QuestionNotFound)
    async def _question_not_found(request: Request, exc: QuestionNotFound):
        del request
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "code": "QUESTION_NOT_FOUND"},
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError):
        del request
        # FastAPI 默认的 422 会把 `input` 原样带回去——导入接口的 input 就是整段 CSV，
        # 里面可能有学号。错误信息只留"哪个字段不对"，不回显内容。
        fields = "；".join(
            f"{'.'.join(str(part) for part in error.get('loc', ()))}: {error.get('msg', '')}"
            for error in exc.errors()
        )
        return JSONResponse(
            # 直接用数字：starlette 正在改这个常量的名字（两个名字都在弃用边缘）
            status_code=422,
            content={"detail": fields or "请求格式不正确", "code": "INVALID_REQUEST"},
        )

