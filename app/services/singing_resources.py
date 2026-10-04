"""Windows 内存不足时，先停本次翻唱。"""

from __future__ import annotations

import ctypes
import os
import shutil
from pathlib import Path


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32)] + [
        (name, ctypes.c_uint64) for name in (
            "total_phys", "available_phys", "total_commit", "available_commit",
            "total_virtual", "available_virtual", "extended_virtual",
        )
    ]


def singing_resource_problem(root: Path, *, starting: bool = False) -> str | None:
    if os.name != "nt":
        return None
    status = _MemoryStatus()
    status.length = ctypes.sizeof(status)
    call = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
    call.argtypes = [ctypes.POINTER(_MemoryStatus)]
    call.restype = ctypes.c_int
    if not call(ctypes.byref(status)) or not status.total_commit:
        return "暂时无法读取内存状态，请稍后再翻唱。"
    minimum = (4 if starting else 1.5) * 1024**3
    if status.available_phys < minimum:
        return "可用内存不足，已暂停本次翻唱。关掉一些占内存的程序后再试。"
    if 1 - status.available_commit / status.total_commit >= 0.85:
        return "系统内存提交量过高，已暂停本次翻唱，请稍后再试。"
    if shutil.disk_usage(root).free <= 10 * 1024**3:
        return "项目盘剩余空间不足10GB，已暂停本次翻唱。"
    return None
