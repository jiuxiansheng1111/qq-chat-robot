"""停机后新增QQ大肥鱼配置；默认/B站/微信配置和旧人格逐字保留。"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.astrbot_qq_persona import QQ_PERSONA_ID, QQ_PLATFORMS
from app.astrbot_config_persona import PERSONA_ID as LEGACY_PERSONA_ID
from scripts.configure_astrbot import _assert_managed_path, _write_json_atomically
from scripts.sync_astrbot_identity import _backup_database, _running

PROFILE_ID = "qqchat-dafeiyu"
PROFILE_FILE = "abconf_qqchat_dafeiyu.json"


def _preference(db, key):
    row = db.execute("SELECT value FROM preferences WHERE scope='global' AND scope_id='global' AND key=?", (key,)).fetchone()
    if not row:
        return {}
    stored = json.loads(row[0])
    # AstrBot SharedPreferences wraps every stored value in {"val": ...}.
    # Accept our initial unwrapped deployment solely so it can be repaired.
    if "val" in stored:
        return stored["val"]
    if key == "abconf_mapping" and set(stored) == {PROFILE_ID}:
        return stored
    if key == "umop_config_routing" and set(stored.values()) == {PROFILE_ID}:
        return stored
    raise ValueError("AstrBot偏好存储格式异常，拒绝覆盖")


def _put_preference(db, key, value, now):
    encoded = json.dumps({"val": value}, ensure_ascii=False)
    cursor = db.execute("UPDATE preferences SET value=?,updated_at=? WHERE scope='global' AND scope_id='global' AND key=?", (encoded, now, key))
    if not cursor.rowcount:
        db.execute("INSERT INTO preferences (created_at,updated_at,scope,scope_id,key,value) VALUES (?,?,'global','global',?,?)", (now, now, key, encoded))


def configure(root: Path, *, apply=False, is_running=_running) -> dict:
    root = root.resolve(strict=True)
    data = root / "data/astrbot/instance/data"
    database, original = data / "data_v4.db", data / "cmd_config.json"
    target = data / "config" / PROFILE_FILE
    prompt_file = root / "prompts/qq/dafeiyu.txt"
    for path in (database, original, target, prompt_file):
        _assert_managed_path(root, path)
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if "- 角色名称：大肥鱼" not in prompt:
        raise ValueError("大肥鱼人格文件不完整")
    base = json.loads(original.read_text(encoding="utf-8-sig"))
    qq_ids = {p["id"] for p in base.get("platform", []) if p.get("type") in QQ_PLATFORMS and p.get("enable")}
    if not qq_ids:
        raise ValueError("没有已启用的QQ平台，未修改任何配置")
    profile = json.loads(target.read_text(encoding="utf-8-sig")) if target.exists() else copy.deepcopy(base)
    runner = profile.get("agent_runner", {})
    if runner.get("runner_type") != "local":
        raise ValueError("仅支持已经验证的原生工具调用流程")
    runner["config"]["persona"]["persona_id"] = QQ_PERSONA_ID
    profile.setdefault("provider_settings", {})["persona_pool"] = [QQ_PERSONA_ID]
    prefixes = profile.get("wake_prefix", [])
    if isinstance(prefixes, list) and "大肥鱼" not in prefixes:
        profile["wake_prefix"] = [*prefixes, "大肥鱼"]
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        mapping, routing = _preference(db, "abconf_mapping"), _preference(db, "umop_config_routing")
        if any(pid + "::" in routing and routing[pid + "::"] != PROFILE_ID for pid in qq_ids):
            raise ValueError("已有QQ专用配置，需要先合并以免覆盖")
        old = db.execute("SELECT system_prompt,tools,skills FROM personas WHERE persona_id=?", (LEGACY_PERSONA_ID,)).fetchone()
        current = db.execute("SELECT system_prompt FROM personas WHERE persona_id=?", (QQ_PERSONA_ID,)).fetchone()
        if old is None:
            raise ValueError("原人格不存在，无法保留其工具权限")
        if current and PROFILE_ID not in mapping:
            raise ValueError("已存在同名大肥鱼人格，拒绝覆盖非本脚本创建的记录")
    mapping[PROFILE_ID] = {"name": "QQ · 大肥鱼", "path": PROFILE_FILE}
    # Specific QQ routes precede any existing catch-all; non-QQ routes stay intact.
    routing = {**{pid + "::": PROFILE_ID for pid in sorted(qq_ids)}, **{k:v for k,v in routing.items() if k not in {pid + "::" for pid in qq_ids}}}
    summary = {"persona": QQ_PERSONA_ID, "qq_platforms": len(qq_ids), "preserved_default_persona": LEGACY_PERSONA_ID, "applied": False}
    if not apply:
        return summary
    if is_running():
        raise RuntimeError("先通过服务管理器停止AstrBot再应用QQ人格")
    now = datetime.now(UTC).isoformat()
    backup = root / "data/astrbot/identity-backups" / ("qq-dafeiyu-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
    _assert_managed_path(root, backup)
    backup.mkdir(parents=True)
    _backup_database(database, backup / database.name)
    existed = target.exists()
    if existed:
        shutil.copy2(target, backup / target.name)
    try:
        with sqlite3.connect(database) as db:
            if current:
                db.execute("UPDATE personas SET system_prompt=?,updated_at=? WHERE persona_id=?", (prompt, now, QQ_PERSONA_ID))
            else:
                db.execute("INSERT INTO personas (created_at,updated_at,persona_id,system_prompt,begin_dialogs,tools,skills,sort_order) VALUES (?,?,?,?,?,?,?,?)", (now, now, QQ_PERSONA_ID, prompt, "[]", old[1], old[2], 0))
            for pid in qq_ids:
                db.execute("UPDATE conversations SET persona_id=? WHERE platform_id=? AND (persona_id IS NULL OR persona_id=?)", (QQ_PERSONA_ID, pid, LEGACY_PERSONA_ID))
            _put_preference(db, "abconf_mapping", mapping, now)
            _put_preference(db, "umop_config_routing", routing, now)
            _write_json_atomically(target, profile)
    except Exception:
        if existed:
            shutil.copy2(backup / target.name, target)
        else:
            target.unlink(missing_ok=True)
        _backup_database(backup / database.name, database)
        raise
    return {**summary, "applied": True, "backup_dir": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(configure(Path(__file__).resolve().parents[1], apply=args.apply), ensure_ascii=False))
