"""用于串行处理 AI 翻唱任务的轻量进程内队列。"""

from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal


class QueueError(RuntimeError):
    """翻唱任务无法进入队列。"""


class SingingError(RuntimeError):
    """可以安全展示给请求用户的业务错误。"""


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
JobKey = tuple[str, str, str]
JobOperation = Callable[["SingingJob"], Awaitable[None]]


@dataclass
class SingingJob:
    id: str
    query: str
    key: JobKey
    status: JobStatus
    progress: str
    error: str | None
    created_at: float


@dataclass
class _Pending:
    job: SingingJob
    operation: JobOperation
    context: contextvars.Context


class SingingJobManager:
    """同一时间只运行一个任务，并限制等待数量和近期状态记录。

    公开方法必须在同一个事件循环中调用。``submit`` 不含 await，因此准入检查和入队在该循环内是原子操作。
    """

    def __init__(self, max_pending: int = 3, cooldown_seconds: float = 120) -> None:
        if max_pending < 1 or cooldown_seconds < 0:
            raise ValueError("max_pending must be positive and cooldown_seconds nonnegative")
        self.max_pending = max_pending
        self.cooldown_seconds = cooldown_seconds
        self._pending: deque[_Pending] = deque()
        self._active: dict[JobKey, SingingJob] = {}
        self._completed: deque[SingingJob] = deque(maxlen=64)
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._runner: asyncio.Task[None] | None = None
        self._current_task: asyncio.Task[None] | None = None
        self._current_job: SingingJob | None = None
        self._cancel_requested: set[str] = set()
        self._closed = False

    def submit(self, key: JobKey, query: str, operation: JobOperation) -> SingingJob:
        """立即接收任务；无法接收时抛出带安全提示的 QueueError。"""
        if self._closed:
            raise QueueError("翻唱队列已关闭")
        if len(key) != 3 or not all(isinstance(part, str) for part in key):
            raise ValueError("key must contain three strings")
        if not callable(operation):
            raise TypeError("operation must be callable")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise QueueError("翻唱队列需要在事件循环中提交任务") from exc
        owner = (key[0], key[2])
        if any((active_key[0], active_key[2]) == owner for active_key in self._active):
            raise QueueError("该用户已有进行中的翻唱任务")
        now = time.monotonic()
        self._cooldowns = {owner: until for owner, until in self._cooldowns.items() if until > now}
        if owner in self._cooldowns:
            remaining = max(1, int(self._cooldowns[owner] - now + 0.999))
            raise QueueError(f"翻唱请求冷却中，请在 {remaining} 秒后重试")
        if len(self._active) >= self.max_pending:
            raise QueueError("翻唱队列已满，请稍后重试")

        job = SingingJob(
            id=uuid.uuid4().hex,
            query=query,
            key=key,
            status="queued",
            progress="排队中",
            error=None,
            created_at=time.time(),
        )
        self._active[key] = job
        self._pending.append(_Pending(job, operation, contextvars.copy_context()))
        if self._runner is None or self._runner.done():
            self._runner = loop.create_task(self._run_queue(), name="singing-job-queue")
        return job

    def status(self, key: JobKey) -> SingingJob | None:
        """返回该键对应的当前任务，或最近保留的已完成任务。"""
        current = self._active.get(key)
        if current is not None:
            return current
        return next((job for job in reversed(self._completed) if job.key == key), None)

    def _finish(self, job: SingingJob) -> None:
        if self._active.get(job.key) is not job:
            return
        self._active.pop(job.key, None)
        self._cancel_requested.discard(job.id)
        if job.status == "succeeded" and self.cooldown_seconds:
            self._cooldowns[(job.key[0], job.key[2])] = time.monotonic() + self.cooldown_seconds
        self._completed.append(job)

    async def _run_queue(self) -> None:
        try:
            while self._pending and not self._closed:
                pending = self._pending.popleft()
                job = pending.job
                if job.id in self._cancel_requested:
                    job.status = "cancelled"
                    job.progress = "已取消"
                    self._finish(job)
                    continue
                job.status = "running"
                job.progress = "处理中"
                self._current_job = job
                try:
                    async def execute(
                        operation: JobOperation = pending.operation,
                        running_job: SingingJob = job,
                    ) -> None:
                        await operation(running_job)

                    self._current_task = asyncio.create_task(
                        execute(), context=pending.context, name=f"singing-job-{job.id}"
                    )
                    await self._current_task
                except asyncio.CancelledError:
                    job.status = "cancelled"
                    job.progress = "已取消"
                    if asyncio.current_task().cancelling():
                        self._closed = True
                except SingingError as exc:
                    job.status = "failed"
                    job.progress = "处理失败"
                    job.error = str(exc)[:200]
                except Exception as exc:  # noqa: BLE001 - job errors must never escape the queue
                    job.status = "failed"
                    job.progress = "处理失败"
                    job.error = f"翻唱处理失败（{type(exc).__name__}）"
                else:
                    if job.id in self._cancel_requested or self._closed:
                        job.status = "cancelled"
                        job.progress = "已取消"
                    else:
                        job.status = "succeeded"
                        job.progress = "已完成"
                finally:
                    self._current_task = None
                    self._current_job = None
                    self._finish(job)
        finally:
            # 也处理队列运行任务自身被外部取消的情况。
            if self._current_task is not None and not self._current_task.done():
                self._current_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await self._current_task
            if self._current_job is not None:
                self._current_job.status = "cancelled"
                self._current_job.progress = "已取消"
                self._finish(self._current_job)
                self._current_job = None
                self._current_task = None
            if self._closed:
                while self._pending:
                    job = self._pending.popleft().job
                    job.status = "cancelled"
                    job.progress = "已取消"
                    self._finish(job)

    async def cancel(self, key: JobKey) -> bool:
        """取消排队中或运行中的任务，并等待运行任务完成清理。"""
        job = self._active.get(key)
        if job is None:
            return False
        self._cancel_requested.add(job.id)
        if job.status == "queued":
            self._pending = deque(entry for entry in self._pending if entry.job is not job)
            job.status = "cancelled"
            job.progress = "已取消"
            self._finish(job)
            return True
        task = self._current_task
        if task is not None and not task.done():
            task.cancel()
        while self._active.get(key) is job:
            await asyncio.sleep(0)
        return True

    async def aclose(self) -> None:
        """拒绝新任务、取消排队/运行任务，并等待清理结束。"""
        if self._closed:
            if self._runner is not None:
                await asyncio.shield(self._runner)
            return
        self._closed = True
        while self._pending:
            job = self._pending.popleft().job
            job.status = "cancelled"
            job.progress = "已取消"
            self._finish(job)
        if self._current_task is not None and not self._current_task.done():
            self._cancel_requested.add(self._current_job.id)  # type: ignore[union-attr]
            self._current_task.cancel()
        if self._runner is not None:
            await asyncio.shield(self._runner)
