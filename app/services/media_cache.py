"""安全管理 OneBot 重试用的出站图片缓存。"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import threading
import time
from pathlib import Path

_CACHE_LOCK = threading.Lock()
_CACHE_NAME_RE = re.compile(r"[0-9a-f]{20}\.(?:jpg|gif)")
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_CACHE_BYTES = 128 * 1024 * 1024
_MAX_CACHE_FILES = 128
_EXPIRE_SECONDS = 24 * 60 * 60
_PROTECT_SECONDS = 60
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def cache_outgoing_image(raw: bytes, base_dir: Path) -> Path | None:
    """把一张图片原子写入受限缓存；不管理缓存目录之外的文件。"""
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_IMAGE_BYTES:
        return None

    digest = hashlib.sha256(raw).hexdigest()[:20]
    extension = ".gif" if raw.startswith((b"GIF87a", b"GIF89a")) else ".jpg"

    with _CACHE_LOCK:
        temp_path: Path | None = None
        temp_fd: int | None = None
        safe_temp_parent: Path | None = None
        try:
            base_path = Path(base_dir).expanduser().absolute()
            if _contains_reparse_component(base_path):
                return None
            base_root = base_path.resolve()
            if _path_exists(base_root) and not _safe_directory(base_root):
                return None
            base_root.mkdir(parents=True, exist_ok=True)
            if not _safe_directory(base_root):
                return None

            cache_dir = base_root / "outgoing_media"
            if _path_exists(cache_dir) and _is_reparse_or_symlink(cache_dir):
                return None
            cache_dir.mkdir(exist_ok=True)
            cache_dir = _validated_cache_dir(base_root, cache_dir)
            if cache_dir is None:
                return None
            safe_temp_parent = cache_dir

            target = cache_dir / f"{digest}{extension}"
            target_exists = _path_exists(target)
            target_info = _managed_file_info(target, cache_dir) if target_exists else None
            if target_exists and target_info is None:
                return None
            if target_info is not None and target_info[0] == len(raw):
                try:
                    if target.read_bytes() == raw:
                        if not _make_room(cache_dir, target, len(raw), time.time()):
                            return None
                        if not _touch_managed_file(target, cache_dir):
                            return None
                        return target
                except OSError:
                    return None

            if not _make_room(cache_dir, target, len(raw), time.time()):
                return None

            if not _safe_cache_directory(base_root, cache_dir):
                return None
            temp_fd, temp_name = tempfile.mkstemp(prefix=".outgoing-", suffix=".tmp", dir=cache_dir)
            temp_path = Path(temp_name)
            file_handle = os.fdopen(temp_fd, "wb")
            temp_fd = None
            with file_handle:
                file_handle.write(raw)
                file_handle.flush()
                os.fsync(file_handle.fileno())

            if not _safe_cache_directory(base_root, cache_dir):
                return None
            if not _safe_temporary_file(temp_path, cache_dir, len(raw)):
                return None
            if _path_exists(target) and _managed_file_info(target, cache_dir) is None:
                return None

            os.replace(temp_path, target)
            temp_path = None
            if _managed_file_info(target, cache_dir) is None:
                return None
            return target
        except (OSError, RuntimeError, ValueError):
            return None
        finally:
            if temp_fd is not None:
                try:
                    os.close(temp_fd)
                except OSError:
                    pass
            if temp_path is not None:
                _unlink_temporary_file(temp_path, safe_temp_parent)


def _contains_reparse_component(path: Path) -> bool:
    return any(_is_reparse_or_symlink(part) for part in reversed((path, *path.parents)))


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE
    )


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _safe_directory(path: Path) -> bool:
    if _is_reparse_or_symlink(path):
        return False
    try:
        return path.is_dir()
    except OSError:
        return False


def _validated_cache_dir(base_root: Path, cache_dir: Path) -> Path | None:
    if not _safe_cache_directory(base_root, cache_dir):
        return None
    try:
        resolved = cache_dir.resolve(strict=True)
        if not resolved.is_relative_to(base_root):
            return None
        return resolved
    except (OSError, RuntimeError):
        return None


def _safe_cache_directory(base_root: Path, cache_dir: Path) -> bool:
    if _is_reparse_or_symlink(cache_dir):
        return False
    try:
        resolved = cache_dir.resolve(strict=True)
        return resolved.is_relative_to(base_root) and resolved == cache_dir
    except (OSError, RuntimeError):
        return False


def _managed_file_info(path: Path, cache_dir: Path) -> tuple[int, float] | None:
    if (
        not _safe_cache_directory(cache_dir.parent, cache_dir)
        or not _CACHE_NAME_RE.fullmatch(path.name)
        or _is_reparse_or_symlink(path)
    ):
        return None
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return None
        resolved = path.resolve(strict=True)
        if resolved.parent != cache_dir or not resolved.is_relative_to(cache_dir):
            return None
        return info.st_size, info.st_mtime
    except (OSError, RuntimeError):
        return None


def _scan_managed_files(cache_dir: Path) -> list[tuple[Path, int, float]] | None:
    if not _safe_cache_directory(cache_dir.parent, cache_dir):
        return None
    result: list[tuple[Path, int, float]] = []
    try:
        children = tuple(cache_dir.iterdir())
    except OSError:
        return None
    for path in children:
        if not _CACHE_NAME_RE.fullmatch(path.name):
            continue
        info = _managed_file_info(path, cache_dir)
        if info is not None:
            result.append((path, info[0], info[1]))
    return result


def _unlink_managed_file(path: Path, cache_dir: Path) -> bool:
    if (
        not _safe_cache_directory(cache_dir.parent, cache_dir)
        or _managed_file_info(path, cache_dir) is None
    ):
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def _make_room(cache_dir: Path, target: Path, new_size: int, now: float) -> bool:
    files = _scan_managed_files(cache_dir)
    if files is None:
        return False
    for path, _, modified_at in files:
        if path != target and now - modified_at >= _EXPIRE_SECONDS:
            _unlink_managed_file(path, cache_dir)

    files = _scan_managed_files(cache_dir)
    if files is None:
        return False

    def fits(items: list[tuple[Path, int, float]]) -> bool:
        target_info = next((item for item in items if item[0] == target), None)
        total_bytes = sum(item[1] for item in items)
        if target_info is None:
            return len(items) < _MAX_CACHE_FILES and total_bytes + new_size <= _MAX_CACHE_BYTES
        return (
            len(items) <= _MAX_CACHE_FILES
            and total_bytes - target_info[1] + new_size <= _MAX_CACHE_BYTES
        )

    if fits(files):
        return True

    for path, _, modified_at in sorted(files, key=lambda item: item[2]):
        if path == target or now - modified_at < _PROTECT_SECONDS:
            continue
        _unlink_managed_file(path, cache_dir)
        files = _scan_managed_files(cache_dir)
        if files is None:
            return False
        if fits(files):
            return True
    return fits(files)


def _touch_managed_file(path: Path, cache_dir: Path) -> bool:
    if (
        not _safe_cache_directory(cache_dir.parent, cache_dir)
        or _managed_file_info(path, cache_dir) is None
    ):
        return False
    try:
        os.utime(path, None, follow_symlinks=False)
        return (
            _safe_cache_directory(cache_dir.parent, cache_dir)
            and _managed_file_info(path, cache_dir) is not None
        )
    except (NotImplementedError, OSError):
        return False


def _safe_temporary_file(path: Path, cache_dir: Path, expected_size: int) -> bool:
    if _is_reparse_or_symlink(path):
        return False
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_size == expected_size
            and resolved.parent == cache_dir
            and resolved.is_relative_to(cache_dir)
        )
    except (OSError, RuntimeError):
        return False


def _unlink_temporary_file(path: Path, expected_parent: Path | None) -> None:
    if expected_parent is None:
        return
    try:
        info = path.lstat()
        resolved_parent = path.parent.resolve(strict=True)
        if (
            stat.S_ISREG(info.st_mode)
            and not _is_reparse_or_symlink(path)
            and path.parent == expected_parent
            and resolved_parent == expected_parent
            and _safe_cache_directory(expected_parent.parent, expected_parent)
        ):
            path.unlink()
    except (OSError, RuntimeError):
        return
