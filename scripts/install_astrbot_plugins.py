"""下载并安全安装已固定版本的画境拾珍插件到私有 AstrBot 实例。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

PLUGIN_NAME = "astrbot_plugin_get_px"
PLUGIN_VERSION = "v3.5.1"
COMMIT = "866d6cd70d0575a66f16742a581dc91d26849e1b"
ARCHIVE_ROOT = f"astrbot_plugin_get_px-{COMMIT}"
ARCHIVE_URL = (
    "https://codeload.github.com/shitianyaa/astrbot_plugin_get_px/zip/" + COMMIT
)
ARCHIVE_SHA256 = "5edb30e3b55ed34e940f3419ee5291d0e767a7ad81554c38ebd897a5ee0ff469"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 3000
EXCLUDED_TOP_LEVEL = frozenset(
    {".github", "docs", "scripts", "tests", ".pre-commit-config.yaml", "AGENTS.md"}
)
REQUIRED_FILES = frozenset({"main.py", "metadata.yaml", "_conf_schema.json", "requirements.txt"})


class InstallError(RuntimeError):
    """插件归档不可信或安装目标不安全。"""


def _project_root(path: Path | None = None) -> Path:
    root = (path or Path(__file__).resolve().parents[1]).resolve()
    if not (root / "app" / "main.py").is_file() or not (root / "pyproject.toml").is_file():
        raise InstallError("请从 qq-chatrobot 仓库运行此脚本，或传入有效的仓库目录。")
    return root


def _assert_managed_path(root: Path, path: Path) -> None:
    root = root.resolve()
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise InstallError(f"路径超出项目目录：{candidate}") from exc

    cursor = root
    for part in relative.parts:
        cursor /= part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            continue
        is_reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(info.st_mode) or is_reparse:
            raise InstallError(f"路径包含符号链接或重解析点：{cursor}")


def _ensure_directory(root: Path, path: Path) -> None:
    _assert_managed_path(root, path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InstallError(f"无法创建私有目录：{path}") from exc
    if not path.is_dir():
        raise InstallError(f"目录位置已被非目录占用：{path}")
    _assert_managed_path(root, path)


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_archive(archive_path: Path) -> Path:
    if _path_entry_exists(archive_path):
        if not archive_path.is_file():
            raise InstallError(f"归档位置已被非普通文件占用：{archive_path}")
        if _sha256_file(archive_path) != ARCHIVE_SHA256:
            raise InstallError(f"现有归档校验值不符，未覆盖：{archive_path}")
        return archive_path

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="get-px-download-", suffix=".tmp", dir=archive_path.parent
    )
    temporary = Path(temporary_name)
    try:
        request = urllib.request.Request(
            ARCHIVE_URL,
            headers={"User-Agent": "qq-chatrobot-plugin-installer/1.0"},
        )
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(descriptor, "wb") as destination, urllib.request.urlopen(
            request, timeout=45
        ) as response:
            final_host = (urlparse(response.geturl()).hostname or "").lower()
            if final_host != "codeload.github.com":
                raise InstallError(f"下载被重定向到未允许的主机：{final_host}")
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_ARCHIVE_BYTES:
                    raise InstallError("插件源码压缩包超过大小上限。")
                digest.update(block)
                destination.write(block)
        if digest.hexdigest() != ARCHIVE_SHA256:
            raise InstallError("插件源码 SHA-256 不匹配；未解包或安装。")
        if _path_entry_exists(archive_path):
            raise InstallError(f"归档文件在下载期间出现；未覆盖：{archive_path}")
        # O_EXCL 避免并发运行时覆盖已经出现的文件。
        output_fd = os.open(archive_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(output_fd, "wb") as output, temporary.open("rb") as source:
            shutil.copyfileobj(source, output)
        return archive_path
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _safe_relative_path(archive_name: str) -> PurePosixPath | None:
    """校验 ZIP 成员名，开发目录返回 None。"""
    if "\\" in archive_name or archive_name.startswith("/"):
        raise InstallError(f"ZIP 成员路径不安全：{archive_name!r}")
    raw_parts = archive_name.rstrip("/").split("/")
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        raise InstallError(f"ZIP 成员路径不安全：{archive_name!r}")
    if raw_parts[0] != ARCHIVE_ROOT:
        raise InstallError(f"ZIP 顶层目录不符合固定提交：{archive_name!r}")
    if len(raw_parts) == 1:
        return None

    relative_parts = raw_parts[1:]
    forbidden = set(':*?"<>|')
    for part in relative_parts:
        if any(char in forbidden or ord(char) < 32 for char in part):
            raise InstallError(f"ZIP 成员名不适用于 Windows：{archive_name!r}")
        if part.endswith((".", " ")):
            raise InstallError(f"ZIP 成员名不适用于 Windows：{archive_name!r}")
        device_name = part.split(".", 1)[0].upper()
        if device_name in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise InstallError(f"ZIP 成员名是 Windows 保留名称：{archive_name!r}")
    if relative_parts[0].casefold() in {name.casefold() for name in EXCLUDED_TOP_LEVEL}:
        return None
    return PurePosixPath(*relative_parts)


def _extract_archive(archive_path: Path, staging_dir: Path) -> int:
    """只解出运行文件；不执行仓库脚本或安装依赖。"""
    if not staging_dir.is_dir() or any(staging_dir.iterdir()):
        raise InstallError("解包目录必须是新建的空目录。")
    plans: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    seen: set[str] = set()
    total_uncompressed = 0

    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise InstallError("插件压缩包包含过多文件。")
        for info in infos:
            relative = _safe_relative_path(info.filename)
            if relative is None or info.is_dir():
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise InstallError(f"不接受 ZIP 符号链接：{info.filename}")
            key = relative.as_posix().casefold()
            if key in seen:
                raise InstallError(f"ZIP 中存在重名路径：{info.filename}")
            seen.add(key)
            total_uncompressed += info.file_size
            if info.file_size < 0 or total_uncompressed > MAX_EXTRACTED_BYTES:
                raise InstallError("解压后的插件超过大小上限。")
            plans.append((info, relative))

        required = {PurePosixPath(name).as_posix() for name in REQUIRED_FILES}
        if not required.issubset(seen):
            raise InstallError("插件压缩包缺少主程序、元数据、配置 schema 或 requirements。")

        extracted = 0
        for info, relative in plans:
            output_path = staging_dir.joinpath(*relative.parts)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _assert_managed_path(staging_dir.resolve(), output_path)
            with archive.open(info, "r") as source, output_path.open("xb") as output:
                shutil.copyfileobj(source, output)
            extracted += 1

    metadata = (staging_dir / "metadata.yaml").read_text(encoding="utf-8")
    if f"name: {PLUGIN_NAME}" not in metadata or f"version: {PLUGIN_VERSION}" not in metadata:
        raise InstallError("插件元数据与固定版本不一致。")
    manifest_path = staging_dir / "SOURCE_MANIFEST.json"
    if manifest_path.exists():
        raise InstallError("源码包含安装器保留的 SOURCE_MANIFEST.json。")
    manifest = {
        "plugin": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "source_repo": "https://github.com/shitianyaa/astrbot_plugin_get_px",
        "commit": COMMIT,
        "source_archive_url": ARCHIVE_URL,
        "source_archive_sha256": ARCHIVE_SHA256,
        "license": "MIT",
        "installed_path": "data/astrbot/instance/data/plugins/astrbot_plugin_get_px",
        "excluded_dev_paths": sorted(EXCLUDED_TOP_LEVEL),
        "requirements_installed_by_installer": False,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return extracted + 1


def install_plugin(project_root: Path | None = None) -> Path:
    """在本项目被忽略的私有目录中安装固定源码。"""
    if os.name != "nt":
        raise InstallError("当前安装器面向 Windows 项目目录运行。")
    root = _project_root(project_root)
    private_root = root / "data" / "astrbot"
    review_root = private_root / "plugin-review"
    archive_path = review_root / f"{PLUGIN_NAME}-{COMMIT}.zip"
    destination = private_root / "instance" / "data" / "plugins" / PLUGIN_NAME
    for path in (private_root, review_root, destination.parent):
        _ensure_directory(root, path)

    _assert_managed_path(root, destination)
    if _path_entry_exists(destination):
        raise InstallError(
            f"插件目录已存在，未覆盖任何文件：{destination}\n"
            "请先在 AstrBot 中核对已有插件目录，再决定如何备份或更新。"
        )

    _assert_managed_path(root, archive_path)
    verified_archive = _download_archive(archive_path)
    staging = Path(tempfile.mkdtemp(prefix="get-px-install-", dir=review_root))
    # 解包失败时保留私有暂存目录，便于排查。
    _extract_archive(verified_archive, staging)
    _assert_managed_path(root, staging)
    # Windows 不覆盖已有目录，冲突时保留现场。
    os.rename(staging, destination)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="校验固定提交并安装画境拾珍到本项目的私有 AstrBot 数据目录。"
    )
    parser.add_argument("--project-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        destination = install_plugin(args.project_root)
    except (OSError, ValueError, InstallError, zipfile.BadZipFile) as exc:
        print(f"安装失败：{exc}", file=sys.stderr)
        return 1
    print(f"已安装固定版本 {PLUGIN_VERSION}：{destination}")
    print("依赖未由此脚本安装；运行配置脚本关闭自动签到和自动触发后再启动 AstrBot。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
