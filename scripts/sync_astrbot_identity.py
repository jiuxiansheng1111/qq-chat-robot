"""停机后同步已有丛雨人格与Companion对话风格，不改模型、权限或功能开关。"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.astrbot_config_persona import PERSONA_ID
from app.astrbot_identity import persona_expression_settings
from app.config import Settings
from scripts.configure_astrbot import _assert_managed_path, _write_json_atomically


def _running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 6185), timeout=0.25):
            return True
    except OSError:
        return False


def _backup_database(source: Path, target: Path) -> None:
    with sqlite3.connect(source) as source_connection, sqlite3.connect(target) as destination:
        source_connection.backup(destination)


def sync_identity(
    project_root: Path, *, dry_run: bool = True, is_running: Callable[[], bool] = _running,
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    database = root / "data/astrbot/instance/data/data_v4.db"
    config_path = root / "data/astrbot/instance/data/config/astrbot_plugin_private_companion_config.json"
    prompt_file, example_file = root / "prompts/persona.txt", root / "prompts/examples.jsonl"
    for path in (database, config_path, prompt_file, example_file):
        _assert_managed_path(root, path)
        if not path.is_file():
            raise ValueError("缺少预期人格文件、Companion配置或AstrBot数据库")
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise TypeError("Companion配置必须是对象")
    prompt = Settings(_env_file=None, persona_prompt_file=str(prompt_file),
                      persona_examples_file=str(example_file)).persona_prompt()
    expression_settings = persona_expression_settings(prompt_file)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT system_prompt FROM personas WHERE persona_id=?", (PERSONA_ID,)).fetchone()
    if row is None:
        raise ValueError("未找到已有qqchat-local-persona；不自动创建或选择其他人格")
    before_config = json.loads(json.dumps(config))
    changed_paths = []
    for key in (None, "basic_config"):
        target = config if key is None else config.get(key)
        if isinstance(target, dict):
            for field, value in expression_settings.items():
                if target.get(field) != value:
                    target[field] = value
                    changed_paths.append((key + "." if key else "") + field)
    summary = {"persona_id": PERSONA_ID, "persona_changed": row[0] != prompt,
               "prompt_chars": len(prompt), "companion_fields": changed_paths, "applied": False}
    if dry_run or (not summary["persona_changed"] and not changed_paths):
        return summary
    if is_running():
        raise RuntimeError("AstrBot仍在127.0.0.1:6185运行，请由服务管理器停止后再同步")
    backup = root / "data/astrbot/identity-backups" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    _assert_managed_path(root, backup)
    backup.mkdir(parents=True)
    shutil.copy2(config_path, backup / config_path.name)
    _backup_database(database, backup / database.name)
    try:
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE personas SET system_prompt=? WHERE persona_id=?", (prompt, PERSONA_ID))
            _write_json_atomically(config_path, config)
    except Exception:
        _write_json_atomically(config_path, before_config)
        _backup_database(backup / database.name, database)
        raise
    summary.update(applied=True, backup_dir=str(backup))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--apply", action="store_true", help="确认服务已停止后执行；默认仅预览")
    args = parser.parse_args()
    print(json.dumps(sync_identity(args.project_root, dry_run=not args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
