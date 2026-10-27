"""批量评分任务接口：创建、查进度、重试。

任务在后台线程里跑（`app/tasks.py`），这里只负责建任务、把状态读出来、把失败项放回去。
状态由 `task_items` 逐份记录，"待复核"从聚合结果的 `route` 派生，不另存——
存两份迟早会出现"状态说已评、路由说待复核"的矛盾。
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, status

from ..store import Store, TaskRecord
from ..tasks import TaskRunner
from .deps import require_admin, require_store
from .schemas import CreateTaskRequest, RetryRequest


def _state_of(item: dict) -> str:
    """把"逐份状态"翻译成教师能直接看的一种状态（验收要求三态：已评/失败/待复核）。

    `task_items.status` 只有 pending/running/graded/failed；"待复核"是
    `route == 'human'` 派生出来的。**派生放在服务端**：让前端自己拼 status + route，
    迟早出现两个页面拼法不一致。数据库里仍然不存这个状态（存两份必然对不上）。
    """
    if item["status"] == "graded":
        return "needs_review" if item["route"] == "human" else "graded"
    return item["status"]


def _task_payload(store: Store, task: TaskRecord) -> dict:
    items = [{**item, "state": _state_of(item)} for item in store.task_item_views(task.task_id)]
    return {
        "task_id": task.task_id,
        "question_code": task.question_code,
        "rubric_id": task.rubric_id,
        "samples": task.samples,
        "status": task.status,
        "total": task.total,
        "done": task.done,
        "failed": task.failed,
        "error": task.error,
        "items": items,
    }


def register(app: FastAPI) -> None:
    def get_runner(request: Request) -> TaskRunner:
        runner = getattr(request.app.state, "runner", None)
        if runner is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="任务执行器未装配"
            )
        return runner

    @app.post("/api/tasks", dependencies=[Depends(require_admin)])
    def create_task(
        payload: CreateTaskRequest,
        store: Store = Depends(require_store),
        runner: TaskRunner = Depends(get_runner),
    ) -> dict:
        try:
            task = store.create_task(
                question_code=payload.question_code,
                answer_ids=payload.answer_ids,
                samples=payload.samples,
            )
        except ValueError as exc:
            # "答卷不属于这道题""一份都没选"属于请求问题，不该报 500
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        runner.start(task.task_id)
        # 重读一次：start 之后状态可能已经是 running，别把建任务时的快照当现状
        return _task_payload(store, store.get_task(task.task_id))

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(require_admin)])
    def get_task(task_id: int, store: Store = Depends(require_store)) -> dict:
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
        return _task_payload(store, task)

    @app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(require_admin)])
    def retry(
        task_id: int,
        payload: RetryRequest,
        store: Store = Depends(require_store),
        runner: TaskRunner = Depends(get_runner),
    ) -> dict:
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
        if task.status == "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="任务还在跑，等它结束再重试"
            )

        reset = store.reset_failed_items(task_id, answer_ids=payload.answer_ids or None)
        if reset:
            runner.start(task_id)
        refreshed = store.get_task(task_id)
        return {
            "task_id": task_id,
            "reset": reset,
            **_task_payload(store, refreshed),
        }
