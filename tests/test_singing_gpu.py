from __future__ import annotations

import asyncio
import threading

import pytest

from app.services import singing_gpu


def test_sync_lock_releases_after_exception(tmp_path, monkeypatch):
    lock_path = tmp_path / "runtime" / "gpu.lock"
    monkeypatch.setattr(singing_gpu, "GPU_LOCK_PATH", lock_path)

    with pytest.raises(RuntimeError, match="boom"), singing_gpu.gpu_lock(poll_interval=0.01):
        raise RuntimeError("boom")

    with singing_gpu.gpu_lock(poll_interval=0.01):
        assert lock_path.is_file()
        assert lock_path.stat().st_size >= 1


@pytest.mark.asyncio
async def test_async_lock_releases_after_exception(tmp_path, monkeypatch):
    lock_path = tmp_path / "runtime" / "gpu.lock"
    monkeypatch.setattr(singing_gpu, "GPU_LOCK_PATH", lock_path)

    with pytest.raises(RuntimeError, match="boom"):
        async with singing_gpu.async_gpu_lock(poll_interval=0.01):
            raise RuntimeError("boom")

    with singing_gpu.gpu_lock(poll_interval=0.01):
        assert lock_path.is_file()


@pytest.mark.asyncio
async def test_async_wait_can_be_cancelled_without_leaking_lock(tmp_path, monkeypatch):
    lock_path = tmp_path / "runtime" / "gpu.lock"
    monkeypatch.setattr(singing_gpu, "GPU_LOCK_PATH", lock_path)
    entered = threading.Event()
    release = threading.Event()
    thread_errors: list[BaseException] = []

    def hold_sync_lock():
        try:
            with singing_gpu.gpu_lock(poll_interval=0.01):
                entered.set()
                if not release.wait(timeout=3):
                    raise TimeoutError("test lock holder timed out")
        except Exception as exc:  # noqa: BLE001 - surface worker errors to the test thread
            thread_errors.append(exc)

    holder = threading.Thread(target=hold_sync_lock, daemon=True)
    holder.start()
    try:
        assert await asyncio.to_thread(entered.wait, 1)

        async def wait_for_gpu():
            async with singing_gpu.async_gpu_lock(poll_interval=0.01):
                return True

        waiter = asyncio.create_task(wait_for_gpu())
        await asyncio.sleep(0.03)
        assert not waiter.done()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    finally:
        release.set()
        await asyncio.to_thread(holder.join, 2)

    assert not holder.is_alive()
    assert thread_errors == []
    async with singing_gpu.async_gpu_lock(poll_interval=0.01):
        pass


def test_poll_interval_is_capped_at_half_second():
    assert singing_gpu._poll_seconds(3) == 0.5
    with pytest.raises(ValueError):
        singing_gpu._poll_seconds(0)
