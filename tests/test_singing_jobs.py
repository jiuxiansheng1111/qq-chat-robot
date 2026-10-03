from __future__ import annotations

import asyncio
import contextvars
import sys

import pytest

from app.services.audio_chunks import run_audio_command
from app.services.singing_jobs import QueueError, SingingError, SingingJobManager


async def _until(predicate) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), 2)


@pytest.mark.asyncio
async def test_serial_queue_capacity_duplicate_cooldown_and_context():
    manager = SingingJobManager(max_pending=2, cooldown_seconds=60)
    route = contextvars.ContextVar("route", default="missing")
    release = asyncio.Event()
    started: list[tuple[str, str]] = []

    async def operation(job):
        started.append((job.query, route.get()))
        job.progress = "正在转换"
        if job.query == "first":
            await release.wait()

    route.set("self-A")
    first = manager.submit(("self-A", "group", "user-1"), "first", operation)
    route.set("self-B")
    second = manager.submit(("self-B", "group", "user-2"), "second", operation)
    await _until(lambda: first.status == "running")
    assert second.status == "queued"
    assert started == [("first", "self-A")]
    assert manager.status(first.key) is first
    with pytest.raises(QueueError, match="已有进行中"):
        manager.submit(first.key, "duplicate", operation)
    with pytest.raises(QueueError, match="已有进行中"):
        manager.submit(("self-A", "another-group", "user-1"), "duplicate", operation)
    with pytest.raises(QueueError, match="队列已满"):
        manager.submit(("self-C", "group", "user-3"), "third", operation)

    release.set()
    await _until(lambda: second.status == "succeeded")
    assert first.status == "succeeded"
    assert started == [("first", "self-A"), ("second", "self-B")]
    assert first.progress == "已完成"
    with pytest.raises(QueueError, match="冷却中"):
        manager.submit(first.key, "again", operation)
    with pytest.raises(QueueError, match="冷却中"):
        manager.submit(("self-A", "another-group", "user-1"), "again", operation)
    await manager.aclose()


@pytest.mark.asyncio
async def test_cancel_queued_and_running_without_cooldown():
    manager = SingingJobManager(max_pending=3, cooldown_seconds=3600)
    running = asyncio.Event()
    cleaned = asyncio.Event()

    async def blocked(_job):
        running.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def should_not_run(_job):
        pytest.fail("queued operation ran after cancellation")

    first_key = ("self", "group", "user-1")
    second_key = ("self", "group", "user-2")
    first = manager.submit(first_key, "first", blocked)
    second = manager.submit(second_key, "second", should_not_run)
    await asyncio.wait_for(running.wait(), 2)
    assert await manager.cancel(second_key)
    assert second.status == "cancelled"
    assert manager.status(second_key) is second
    assert not await manager.cancel(second_key)
    assert await manager.cancel(first_key)
    assert cleaned.is_set()
    assert first.status == "cancelled"

    async def done(_job):
        pass

    replacement = manager.submit(first_key, "retry", done)
    await _until(lambda: replacement.status == "succeeded")
    await manager.aclose()


@pytest.mark.asyncio
async def test_errors_are_safe_and_close_cleans_up():
    manager = SingingJobManager(max_pending=3)

    async def unexpected(_job):
        raise RuntimeError("SECRET_API_TOKEN")

    async def expected(_job):
        raise SingingError("音源不可用")

    failed = manager.submit(("s", "g", "u1"), "bad", unexpected)
    business = manager.submit(("s", "g", "u2"), "bad", expected)
    await _until(lambda: business.status == "failed")
    assert failed.status == "failed"
    assert "SECRET_API_TOKEN" not in failed.error
    assert "RuntimeError" in failed.error
    assert business.error == "音源不可用"

    stopped = asyncio.Event()

    async def long_running(_job):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    active = manager.submit(("s", "g", "u3"), "active", long_running)
    queued = manager.submit(("s", "g", "u4"), "queued", long_running)
    await _until(lambda: active.status == "running")
    await manager.aclose()
    assert stopped.is_set()
    assert active.status == "cancelled"
    assert queued.status == "cancelled"
    assert not manager._active
    with pytest.raises(QueueError, match="已关闭"):
        manager.submit(("s", "g", "u5"), "late", long_running)


@pytest.mark.asyncio
async def test_completed_history_is_bounded_without_losing_active():
    manager = SingingJobManager(max_pending=2, cooldown_seconds=0)

    async def done(_job):
        pass

    for number in range(65):
        key = ("s", "g", str(number))
        job = manager.submit(key, "done", done)
        await _until(lambda job=job: job.status == "succeeded")
    assert manager.status(("s", "g", "0")) is None
    assert manager.status(("s", "g", "64")) is not None
    assert len(manager._completed) == 64
    await manager.aclose()


@pytest.mark.asyncio
async def test_close_terminates_a_running_audio_subprocess(tmp_path):
    manager = SingingJobManager()
    started = tmp_path / "started"
    survived = tmp_path / "survived"
    script = (
        "from pathlib import Path; import time; "
        f"Path({str(started)!r}).touch(); "
        "time.sleep(2); "
        f"Path({str(survived)!r}).touch()"
    )

    async def operation(_job):
        await run_audio_command([sys.executable, "-c", script], timeout_seconds=30)

    job = manager.submit(("self", "group", "user"), "song", operation)
    await _until(started.exists)
    await manager.aclose()
    assert job.status == "cancelled"
    await asyncio.sleep(2.1)
    assert not survived.exists()
