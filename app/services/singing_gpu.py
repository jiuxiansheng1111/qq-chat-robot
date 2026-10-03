"""让训练和翻唱轮流用同一张显卡。"""

from __future__ import annotations

import asyncio
import errno
import math
import os
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GPU_LOCK_PATH = PROJECT_ROOT / "data" / "singing" / "runtime" / "gpu.lock"
_POLL_SECONDS = 0.25
_PROCESS_LOCK = threading.Lock()


def _poll_seconds(value: float) -> float:
    if isinstance(value, bool):
        raise TypeError("poll_interval must be a positive number")
    try:
        interval = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("poll_interval must be a positive number") from exc
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("poll_interval must be a positive number")
    return min(interval, 0.5)


def _open_lock_file():
    GPU_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    stream = GPU_LOCK_PATH.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


def _lock_is_busy(error: OSError) -> bool:
    return error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
        error, "winerror", None
    ) in {33, 36}


def _try_os_lock(stream) -> bool:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if _lock_is_busy(exc):
                return False
            raise

    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if _lock_is_busy(exc):
            return False
        raise


def _unlock_os(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def gpu_lock(*, poll_interval: float = _POLL_SECONDS) -> Iterator[None]:
    """等显卡空出来，离开时释放锁。"""
    interval = _poll_seconds(poll_interval)
    stream = _open_lock_file()
    process_acquired = False
    os_acquired = False
    try:
        while not process_acquired:
            process_acquired = _PROCESS_LOCK.acquire(timeout=interval)
        while not _try_os_lock(stream):
            time.sleep(interval)
        os_acquired = True
        yield
    finally:
        try:
            if os_acquired:
                _unlock_os(stream)
        finally:
            stream.close()
            if process_acquired:
                _PROCESS_LOCK.release()


@asynccontextmanager
async def async_gpu_lock(*, poll_interval: float = _POLL_SECONDS) -> AsyncIterator[None]:
    """可取消的异步等候，不额外开等待线程。"""
    interval = _poll_seconds(poll_interval)
    stream = _open_lock_file()
    process_acquired = False
    os_acquired = False
    try:
        while not process_acquired:
            process_acquired = _PROCESS_LOCK.acquire(blocking=False)
            if not process_acquired:
                await asyncio.sleep(interval)
        while not _try_os_lock(stream):
            await asyncio.sleep(interval)
        os_acquired = True
        yield
    finally:
        try:
            if os_acquired:
                _unlock_os(stream)
        finally:
            stream.close()
            if process_acquired:
                _PROCESS_LOCK.release()
