"""固定 AstrBot 插件安装器的 ZIP 路径隔离测试。"""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "install_astrbot_plugins.py"
SPEC = importlib.util.spec_from_file_location("_test_astrbot_plugin_installer", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.mark.parametrize(
    "member",
    [
        "../outside.txt",
        f"{installer.ARCHIVE_ROOT}/../../outside.txt",
        f"{installer.ARCHIVE_ROOT}\\..\\outside.txt",
        f"{installer.ARCHIVE_ROOT}/C:/outside.txt",
        f"{installer.ARCHIVE_ROOT}/CON.txt",
        "/outside.txt",
    ],
)
def test_rejects_unsafe_zip_member_paths(member: str) -> None:
    with pytest.raises(installer.InstallError):
        installer._safe_relative_path(member)


def test_skips_development_files_and_returns_safe_runtime_paths() -> None:
    assert installer._safe_relative_path(
        f"{installer.ARCHIVE_ROOT}/main.py"
    ).as_posix() == "main.py"
    assert installer._safe_relative_path(
        f"{installer.ARCHIVE_ROOT}/.github/workflows/release.yml"
    ) is None
    assert installer._safe_relative_path(
        f"{installer.ARCHIVE_ROOT}/tests/test_plugin.py"
    ) is None


def test_extracts_required_runtime_files_and_writes_source_manifest(tmp_path: Path) -> None:
    archive_path = tmp_path / "source.zip"
    stage = tmp_path / "stage"
    stage.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        root = installer.ARCHIVE_ROOT
        archive.writestr(f"{root}/main.py", "# plugin\n")
        archive.writestr(
            f"{root}/metadata.yaml",
            f"name: {installer.PLUGIN_NAME}\nversion: {installer.PLUGIN_VERSION}\n",
        )
        archive.writestr(f"{root}/_conf_schema.json", "{}\n")
        archive.writestr(f"{root}/requirements.txt", "aiohttp\n")
        archive.writestr(f"{root}/images/sample.txt", "asset")
        archive.writestr(f"{root}/docs/readme.md", "dev docs")

    extracted_count = installer._extract_archive(archive_path, stage)

    assert extracted_count == 6
    assert (stage / "main.py").is_file()
    assert (stage / "images" / "sample.txt").read_text(encoding="utf-8") == "asset"
    assert not (stage / "docs").exists()
    manifest = json.loads((stage / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["commit"] == installer.COMMIT
    assert manifest["source_archive_sha256"] == installer.ARCHIVE_SHA256
    assert manifest["requirements_installed_by_installer"] is False


def test_rejects_wrong_commit_root_and_zip_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    stage = tmp_path / "stage"
    stage.mkdir()
    with zipfile.ZipFile(archive_path, "w") as archive:
        root = installer.ARCHIVE_ROOT
        archive.writestr(f"{root}/main.py", "# plugin\n")
        archive.writestr(
            f"{root}/metadata.yaml",
            f"name: {installer.PLUGIN_NAME}\nversion: {installer.PLUGIN_VERSION}\n",
        )
        archive.writestr(f"{root}/_conf_schema.json", "{}\n")
        archive.writestr(f"{root}/requirements.txt", "aiohttp\n")
        archive.writestr("other-commit/README.md", "wrong source")

    with pytest.raises(installer.InstallError, match="顶层目录"):
        installer._extract_archive(archive_path, stage)
