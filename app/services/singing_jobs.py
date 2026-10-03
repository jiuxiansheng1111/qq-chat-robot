"""Small in-process queue for serialized AI singing work."""

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
    """A job could not be admitted to the singing queue."""


class SingingError(RuntimeError):
    """A business error whose message is safe to show to the requesting user."""


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
    """One active job at a time, with bounded waiting and recent status history.

    Public methods must be called from the same event loop. ``submit`` contains
    no await, so admission checks and insertion are atomic on that loop.
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
        """Admit a job immediately or raise QueueError with a safe Chinese reason."""
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
        """Return the active job or the newest retained completion for this key."""
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
            # Also cover an external cancellation of the queue runner itself.
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
        """Cancel a queued or running job and wait for running cleanup."""
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
        """Reject new work, cancel pending/running work, and await cleanup."""
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
