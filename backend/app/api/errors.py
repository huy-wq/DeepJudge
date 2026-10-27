"""接口错误映射：把领域异常翻译成"人能读 + 状态码对"的响应。

集中在一处的好处是**前端只需要认一种错误形状**：`{detail}` 或 `{detail, issues}`。
散在各路由里 `try/except` 的话，迟早出现"这个接口返回 HTML 报错"那种事。
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from fastapi.exceptions import RequestValidationError

from ..store import (
    QuestionNotFound,
    ResultFinalized,
    ResultNotFound,
    ResultSuperseded,
    ReviewRejected,
)


def register(app: FastAPI) -> None:
    @app.exception_handler(QuestionNotFound)
    async def _question_not_found(request: Request, exc: QuestionNotFound):
        del request
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "code": "QUESTION_NOT_FOUND"},
        )

    @app.exception_handler(ResultNotFound)
    async def _result_not_found(request: Request, exc: ResultNotFound):
        del request
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "code": "RESULT_NOT_FOUND"},
        )

    @app.exception_handler(ResultFinalized)
    async def _result_finalized(request: Request, exc: ResultFinalized):
        del request
        # 409 而不是 400：请求本身没错，是"这份结果的状态不允许再做这件事"
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc), "code": "RESULT_FINALIZED"},
        )

    @app.exception_handler(ResultSuperseded)
    async def _result_superseded(request: Request, exc: ResultSuperseded):
        del request
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc), "code": "RESULT_SUPERSEDED"},
        )

    @app.exception_handler(ReviewRejected)
    async def _review_rejected(request: Request, exc: ReviewRejected):
        del request
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "code": "REVIEW_REJECTED"},
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

