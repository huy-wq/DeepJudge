"""批量评分任务的执行器。

**进程内线程池**，不做消息队列：单用户、单机演示。代价写进架构 §9：

- 进程重启 → 停在 running 的任务标为 `interrupted` 并可重试（`Store.mark_interrupted_tasks`）。
- 多 worker 部署时每个进程只看得见自己的任务，需要换成共享队列。

关键约定：**一份答卷失败不许中断整批**。任何异常都在单个 item 内被吃掉，
记成 `failed` + 原因，然后继续下一份；失败项可以单独重试。
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .pipeline import GradingError, evaluate
from .providers.base import GradingProvider, ProviderError
from .store import RunMeta, Store, TaskRecord

# 再高也没用：模型侧有并发限制，本地也扛不住更多线程
MAX_WORKERS = 4


def concurrency_from_env(default: int = 2) -> int:
    raw = (os.environ.get("GRADING_CONCURRENCY") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"GRADING_CONCURRENCY 必须是整数：{raw!r}") from exc
    if value < 1:
        raise RuntimeError("GRADING_CONCURRENCY 必须大于等于 1")
    return min(value, MAX_WORKERS)


class TaskRunner:
    """一个实例管一个 Store 与一个 provider 工厂。

    用工厂而不是实例：假 provider 的响应游标、真实客户端的连接状态都不该跨答卷复用。
    """

    def __init__(
        self,
        store: Store,
        provider_factory: Callable[[], GradingProvider],
        *,
        concurrency: int = 2,
    ) -> None:
        self._store = store
        self._provider_factory = provider_factory
        self._concurrency = max(1, concurrency)
        self._events: dict[int, threading.Event] = {}
        self._lock = threading.Lock()

    def start(self, task_id: int) -> None:
        """起一个后台线程跑任务。同一任务重复调用不会启动第二个。"""
        with self._lock:
            if task_id in self._events:
                return
            event = threading.Event()
            self._events[task_id] = event
        threading.Thread(
            target=self._run, args=(task_id, event), name=f"task-{task_id}", daemon=True
        ).start()

    def wait(self, task_id: int, timeout: float | None = None) -> bool:
        """等任务结束。测试用它代替 sleep。"""
        with self._lock:
            event = self._events.get(task_id)
        return True if event is None else event.wait(timeout)

    def wait_all(self, timeout: float | None = None) -> None:
        """等当前所有任务结束。测试收尾用它，避免库被删时还有线程在写。"""
        with self._lock:
            events = list(self._events.values())
        for event in events:
            event.wait(timeout)

    def _run(self, task_id: int, event: threading.Event) -> None:
        try:
            task = self._store.get_task(task_id)
            if task is None:
                return
            self._store.set_task_status(task_id, "running")
            self._process(task)
            self._store.set_task_status(task_id, "done")
        except Exception as exc:  # noqa: BLE001
            # 执行器自己挂了（库锁超时、连接断了）：**必须**留下痕迹。
            # 没有这个 except，线程会静默猝死，而任务永远停在 running，
            # /retry 又会因为"任务还在跑"拒绝重试——那份任务就废了。
            try:
                self._store.set_task_status(
                    task_id, "interrupted", error=f"任务执行器异常：{exc}"
                )
                self._store.reset_failed_items(task_id)
            except Exception:  # pragma: no cover - 连库都写不进去时只能放弃
                pass
        finally:
            with self._lock:
                self._events.pop(task_id, None)
            event.set()

    def _process(self, task: TaskRecord) -> None:
        """开 N 个 worker，各自循环领取任务，直到没有待评的答卷。

        先起 worker 再各自"抢"，而不是一次抢光再提交：这样 `running` 这个状态
        才真的代表"这份正在评"，也才知道重启后哪些需要标记中断。
        """

        def worker() -> None:
            while True:
                item = self._store.claim_next_item(task.task_id)
                if item is None:
                    return
                self._grade_one(task, item.item_id, item.answer_id)

        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            futures = [pool.submit(worker) for _ in range(self._concurrency)]
            for future in futures:
                # 单份异常已在 _grade_one 内消化，这里只是等它们都结束
                future.result()

    def _grade_one(self, task: TaskRecord, item_id: int, answer_id: int) -> None:
        try:
            answer = self._store.get_answer(answer_id)
            rubric = self._store.load_rubric(task.rubric_id)
            if answer is None or rubric is None:
                self._store.finish_task_item(
                    item_id, status="failed", error="答卷或标准快照已不存在"
                )
                return

            provider = self._provider_factory()
            evaluation = evaluate(
                rubric, answer["raw_text"], provider, samples=task.samples
            )
            stored = self._store.save_evaluation_for_answer(
                answer_id=answer_id,
                rubric=rubric,
                evaluation=evaluation,
                meta=RunMeta(model=getattr(provider, "model_name", "") or ""),
                # 明确指向任务级快照：不要让"按内容找版本"再插一版
                rubric_id=task.rubric_id,
            )
            self._store.finish_task_item(
                item_id, status="graded", aggregate_id=stored.aggregate_id
            )
        except (ProviderError, GradingError) as exc:
            # 预期内的失败：模型挂了、输出不可用。记原因，继续下一份。
            self._store.finish_task_item(item_id, status="failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - 未预期错误也不许中断整批
            self._store.finish_task_item(item_id, status="failed", error=f"内部错误：{exc}")
