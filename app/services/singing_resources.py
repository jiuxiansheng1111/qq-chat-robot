"""Windows 内存不足时，先停本次翻唱。"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger("qqchat")
_GIB = 1024**3


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
        (name, ctypes.c_uint64) for name in (
            "total_phys", "available_phys", "total_commit", "available_commit",
            "total_virtual", "available_virtual", "extended_virtual",
        )
    ]


def _read_memory_status() -> _MemoryStatus | None:
    status = _MemoryStatus()
    status.length = ctypes.sizeof(status)
    call = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
    call.argtypes = [ctypes.POINTER(_MemoryStatus)]
    call.restype = ctypes.c_int
    if not call(ctypes.byref(status)) or not status.total_commit:
        return None
    return status


def singing_resource_problem(
    root: Path, *, starting: bool = False, stage: str = "processing",
) -> str | None:
    if os.name != "nt":
        return None
    stage = stage if isinstance(stage, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", stage) else "unknown"
    minimum = int((4 if starting else 1.5) * _GIB)
    status = _read_memory_status()
    readings = {"stage": stage, "starting": starting, "minimum_phys_bytes": minimum}

    def blocked(reason: str, message: str) -> str:
        snapshot = {**readings, "reason": reason}
        logger.warning("singing_resource_guard %s", json.dumps(snapshot), extra={"resource_snapshot": snapshot})
        return message

    if status is None:
        return blocked("memory_status_unavailable", "暂时无法读取内存状态，请稍后再翻唱。")
    readings.update({
        "available_phys_bytes": status.available_phys, "total_phys_bytes": status.total_phys,
        "available_commit_bytes": status.available_commit, "total_commit_bytes": status.total_commit,
    })
    if status.available_phys < minimum:
        return blocked(
            "low_physical_memory",
            f"可用内存不足（当前 {status.available_phys / _GIB:.2f} GiB，"
            f"本阶段至少需要 {minimum / _GIB:g} GiB），已暂停本次翻唱。"
            "关掉一些占内存的程序后再试。",
        )
    if 1 - status.available_commit / status.total_commit >= 0.85:
        used = status.total_commit - status.available_commit
        return blocked(
            "high_commit_usage",
            f"系统内存提交量过高（已用 {used / _GIB:.2f}/{status.total_commit / _GIB:.2f} GiB，"
            "达到 85% 保护线），已暂停本次翻唱，请稍后再试。",
        )
    readings["disk_free_bytes"] = shutil.disk_usage(root).free
    if readings["disk_free_bytes"] <= 10 * _GIB:
        return blocked("low_disk_space", "项目盘剩余空间不足10GB，已暂停本次翻唱。")
    return None
