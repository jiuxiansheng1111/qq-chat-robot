"""Reload the QQ Hearthstone feature without changing models or plugin configuration."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.cleanup_qq_memory import manual_memory_digest
from scripts.deploy_qq_features import (
    DATA,
    legacy_hash,
    lifecycle,
    qq_connection_status,
    read,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(*, output_root: Path | None = None):
    output = (
        (output_root or ROOT / "data/hearthstone")
        / "deployments"
        / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    output.mkdir(parents=True)
    result = {"started_at": datetime.now(UTC).isoformat(), "real_qq_test_messages_sent": False}
    if lifecycle(output, "stop", "-Stop"):
        raise RuntimeError("Managed stop failed")
    try:
        protected = [ROOT / ".env", ROOT / "data/dafeiyu/owner-memory-config.json",
                     ROOT / "prompts/qq/dafeiyu.txt", DATA / "cmd_config.json"]
        protected += [p for p in (DATA / "config").glob("*.json")
                      if p.name != "astrbot_plugin_bilibili_ai_bot_config.json"]
        before = {str(p): digest(p) for p in protected}
        for index, path in enumerate(protected):
            shutil.copy2(path, output / f"{index}-{path.name}.backup")
        legacy_before = legacy_hash()
        manual_before = manual_memory_digest(ROOT)
        archive = ROOT / "data/dafeiyu/owner-memory.sqlite3"
        archive_before = digest(archive)
        bili_path = DATA / "config/astrbot_plugin_bilibili_ai_bot_config.json"
        bili_before = read(bili_path)
        assert before == {str(p): digest(p) for p in protected}
        assert manual_memory_digest(ROOT) == manual_before and digest(archive) == archive_before
        result.update(protected_config_preserved=True, owner_archive_preserved=True,
                      manual_memories_preserved=manual_before[1], card_catalog_ready=(ROOT / "data/hearthstone/cards.json").is_file())
    finally:
        result["restart_exit_code"] = lifecycle(output, "start", "-ReuseExistingConfig")
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if result["restart_exit_code"]:
            raise RuntimeError("Managed restart failed")
    bili_after = read(bili_path)
    result["bili_changed_keys_on_start"] = [k for k in bili_before.keys() | bili_after.keys()
                                            if bili_before.get(k) != bili_after.get(k)]
    assert set(result["bili_changed_keys_on_start"]) <= {"BUVID3", "BUVID4"}
    assert before == {str(p): digest(p) for p in protected}
    assert legacy_hash() == legacy_before
    result["qq_connection"] = qq_connection_status()
    result["finished_at"] = datetime.now(UTC).isoformat()
    for path in (output / "result.json", output.parent.parent / "deployment.json"):
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not all(result["qq_connection"].values()):
        raise RuntimeError("QQ transport not ready")


if __name__ == "__main__":
    main()
