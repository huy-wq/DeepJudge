"""评分标准接口：生成、带意见迭代、保存、列表、按版本打开。

三件事对应教师的三步动作：

1. **生成**：题干 + 满分 + 一段口述 → 结构化草稿 + 三视图（摘要 / 预览表 / 校验清单），不落库。
2. **迭代**：拿某一版 + 一条修改意见重新生成；上一版整份进入提示词，模型只该动被指出的部分。
3. **保存**：把教师确认过的 JSON 存成新版本。结构错误在这里被拦住并**逐条指出**；
   内容与最新版相同则复用那一行，不制造空版本。

保存只追加版本，不改写旧行的**标准内容**——任务快照指过去以后，旧记录永远读得到当时那份标准
（架构 §5.5）。溯源列（原话 / 意见）例外：内容相同而复用时只把**空着的**补上，不覆盖已有值。
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, status

from ..core.rubric import RubricError, RubricIssue, parse
from ..providers.base import GradingProvider
from ..rubric_gen import describe_changes, generate_rubric
from ..store import RubricNotFound, Store
from .deps import require_admin, require_store
from .schemas import (
    GenerateRubricRequest,
    IterateRubricRequest,
    SaveRubricRequest,
    ValidateRubricRequest,
)


def _document(record: dict[str, Any]) -> dict:
    """打开某一版标准时一并给出校验清单（含仅提示项）。

    已保存的标准不会有阻断项；但仅提示项（等级依据含糊、满分不可达……）值得在页面上
    继续显示——保存成功不等于"值得直接拿去评分"。
    """
    parsed = parse(record["rubric"])
    return {**record, "issues": [issue.to_dict() for issue in parsed.issues]}


def _save(
    store: Store,
    payload: SaveRubricRequest,
    *,
    expected_question: str | None = None,
    source_text: str = "",
    based_on_rubric_id: int | None = None,
) -> dict:
    parsed = parse(payload.rubric)
    if not parsed.usable:
        # 一次给全部阻断项，每条的 message 里带具体条目（P1 / D1），而不是只报第一条
        raise RubricError(parsed.issues)
    if expected_question is not None and parsed.rubric.question_id != expected_question:
        raise RubricError(
            [
                RubricIssue(
                    "QUESTION_CHANGED",
                    f"题号不能修改：这份标准属于 {expected_question}",
                )
            ]
        )
    if not source_text.strip():
        # 没有原话，日后没人能解释"这个权重怎么来的"（架构 §5.6）
        raise RubricError(
            [
                RubricIssue(
                    "SOURCE_TEXT_REQUIRED",
                    "请填写评分标准原文：它是这些权重由来的唯一依据",
                )
            ]
        )
    saved = store.save_rubric(
        parsed.rubric,
        source_text=source_text,
        feedback=payload.feedback,
        based_on_rubric_id=based_on_rubric_id,
    )
    return {
        "rubric_id": saved.rubric_id,
        "question_code": saved.question_code,
        "version": saved.version,
        "checksum": saved.checksum,
        "parent_rubric_id": saved.parent_rubric_id,
        "reused": saved.reused,
        "created_at": saved.created_at,
        # 保存成功不代表没有值得看一眼的地方：语义类问题只提示、不拦
        "issues": [issue.to_dict() for issue in parsed.warnings],
    }


def register(app: FastAPI) -> None:
    def get_provider_factory(request: Request) -> Callable[[], GradingProvider]:
        factory = getattr(request.app.state, "provider_factory", None)
        if factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="模型未装配"
            )
        return factory

    @app.post("/api/rubrics/generate", dependencies=[Depends(require_admin)])
    def generate(
        payload: GenerateRubricRequest,
        factory: Callable[[], GradingProvider] = Depends(get_provider_factory),
    ) -> dict:
        try:
            generated = generate_rubric(
                factory(),
                stem=payload.stem,
                total_score=payload.total_score or 0,
                standard_text=payload.standard_text,
                reference_answer=payload.reference_answer,
                question_id=payload.question_id or "Q-01",
            )
        except ValueError as exc:
            # 缺题干 / 满分非正 / 原文为空属于请求问题
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return generated.views()

    @app.post(
        "/api/rubrics/{rubric_id}/iterate", dependencies=[Depends(require_admin)]
    )
    def iterate(
        rubric_id: int,
        payload: IterateRubricRequest,
        store: Store = Depends(require_store),
        factory: Callable[[], GradingProvider] = Depends(get_provider_factory),
    ) -> dict:
        record = store.get_rubric(rubric_id)
        if record is None:
            raise RubricNotFound(f"评分标准版本不存在：{rubric_id}")
        previous = parse(record["rubric"]).rubric
        try:
            generated = generate_rubric(
                factory(),
                previous=previous,
                feedback=payload.feedback,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        body = generated.views()
        # 模型到底动了哪里：服务端算 diff，教师不用自己逐行比对
        body["changes"] = describe_changes(previous, generated.rubric)
        body["based_on"] = {
            "rubric_id": rubric_id,
            "version": record["version"],
            "question_code": record["question_code"],
        }
        return body

    @app.post("/api/rubrics/validate", dependencies=[Depends(require_admin)])
    def validate_rubric(payload: ValidateRubricRequest) -> dict:
        """实时校验编辑中的标准。

        编辑页不复制一份校验规则：每次改动（防抖后）都来问服务端，阻断项与提示项
        与保存时完全同一套判断。**不需要数据库**，也不会写入任何东西。
        """
        parsed = parse(payload.rubric)
        return {
            "usable": parsed.usable,
            "issues": [issue.to_dict() for issue in parsed.issues],
        }

    @app.post("/api/rubrics", dependencies=[Depends(require_admin)])
    def create_rubric(
        payload: SaveRubricRequest, store: Store = Depends(require_store)
    ) -> dict:
        return _save(
            store,
            payload,
            source_text=payload.source_text,
        )

    @app.put("/api/rubrics/{rubric_id}", dependencies=[Depends(require_admin)])
    def update_rubric(
        rubric_id: int,
        payload: SaveRubricRequest,
        store: Store = Depends(require_store),
    ) -> dict:
        record = store.get_rubric(rubric_id)
        if record is None:
            raise RubricNotFound(f"评分标准版本不存在：{rubric_id}")
        # 编辑不重发原话时沿用上一版的溯源，别让"改一行"把教师原话抹掉
        source_text = payload.source_text.strip() or record["source_text"]
        return _save(
            store,
            payload,
            expected_question=record["question_code"],
            source_text=source_text,
            based_on_rubric_id=rubric_id,
        )

    @app.get("/api/rubrics", dependencies=[Depends(require_admin)])
    def list_rubrics(store: Store = Depends(require_store)) -> dict:
        rows = store.list_rubrics()
        return {"total": len(rows), "items": rows}

    @app.get("/api/rubrics/{rubric_id}", dependencies=[Depends(require_admin)])
    def get_rubric(rubric_id: int, store: Store = Depends(require_store)) -> dict:
        record = store.get_rubric(rubric_id)
        if record is None:
            raise RubricNotFound(f"评分标准版本不存在：{rubric_id}")
        return _document(record)
