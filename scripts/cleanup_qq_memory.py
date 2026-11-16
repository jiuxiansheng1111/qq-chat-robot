"""保留明确保存的持久记忆，移除重复自学习和陪伴插件的自动记忆。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from scripts.configure_astrbot import _assert_managed_path, _write_json_atomically
from scripts.sync_astrbot_identity import _running

AUTO_FLAGS = frozenset({
    "enable_companion_memory", "enable_expression_learning", "enable_user_habit_learning",
    "enable_dialogue_episode_memory", "enable_group_episode_memory", "enable_livingmemory_integration",
    "enable_memory_companion_emotional_drift", "enable_memory_companion_cross_window_emotion",
    "enable_memory_companion_dream_fragment", "enable_memory_companion_open_loop_search",
    "enable_memory_companion_feature_context", "enable_memory_companion_private_recall",
    "enable_group_slang_learning", "enable_group_slang_meanings", "enable_group_member_profiles",
    "enable_group_topic_threads", "enable_group_relationship_graph", "enable_cross_user_memory_bridge",
    "enable_worldbook_member_recognition", "enable_auto_user_profile_creation",
    "enable_personality_iteration_experiment", "enable_personality_iteration_auto_tune",
    "enable_skill_growth_simulation", "enable_personal_goal_auto_progress",
    "enable_expression_style_review", "enable_memory_companion_bridge",
    "worldbook_auto_import", "worldbook_auto_pending_observations",
    "enable_daily_diary", "daily_diary_generate_share_seed", "enable_daily_review",
    "daily_review_auto_apply_guidance", "enable_mixed_dream_themes", "enable_yesterday_screen_diary_context",
})
AUTO_STORE_FIELDS = frozenset({
    "dream_fragments", "daily_dream", "bot_diaries", "daily_review_reports", "daily_review_active_guidance",
    "creative_memory_pool", "yesterday_conversation_summary", "expression_voice_profile", "expression_learning_runtime",
    "recent_prompt_injection_events", "recent_prompt_injections",
})
AUTO_USER_FIELDS = frozenset({
    "companion_memory", "expression_profile", "intent_profile", "dialogue_episodes", "chronotype_profile",
    "memory_echo_context", "last_memory_echo_key", "memory_echo_checked_key",
})
AUTO_GROUP_FIELDS = frozenset({
    "group_episodes", "member_profiles", "slang", "slang_meanings", "relationship_graph", "topic_threads",
})


def manual_memory_digest(root: Path) -> tuple[str, int]:
    path = root / "data/qqchat.db"
    if not path.exists():
        return "", 0
    rows = []
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as db:
        for table in ("long_term_memories", "group_memories"):
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'",(table,)).fetchone():
                rows.extend((table,*row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY id'))
    return hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest(), len(rows)


def _empty(value):
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, str):
        return ""
    return value


def _write_store(path, value):
    # Companion's own reader uses utf-8, and rejects the BOM used by AstrBot configs.
    temporary=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def cleanup(root: Path, *, apply=False, is_running=_running) -> dict:
    root = root.resolve(strict=True)
    data = root / "data/astrbot/instance/data"
    config = data / "config/astrbot_plugin_private_companion_config.json"
    store = data / "plugin_data/astrbot_plugin_private_companion/companions.json"
    learning = "astrbot_plugin_self_learning"
    removals = [data/"plugins"/learning, data/"config"/(learning+"_config.json"), data/"plugin_data"/learning]
    for path in (config, store, *removals):
        _assert_managed_path(root,path)
    settings = json.loads(config.read_text(encoding="utf-8-sig"))
    contents = json.loads(store.read_text(encoding="utf-8-sig"))
    changed_flags = []
    def disable(obj,prefix=""):
        if not isinstance(obj,dict):
            return
        for key,value in obj.items():
            if key in AUTO_FLAGS and value is True:
                obj[key] = False
                changed_flags.append(prefix+key)
            elif isinstance(value,dict):
                disable(value,prefix+key+".")
    disable(settings)
    cleared = []
    def clear(obj,keys,prefix):
        if not isinstance(obj,dict):
            return
        for key in keys & obj.keys():
            if obj[key]:
                obj[key] = _empty(obj[key])
                cleared.append(prefix+key)
    clear(contents,AUTO_STORE_FIELDS,"")
    for obj in contents.get("users",{}).values():
        clear(obj,AUTO_USER_FIELDS,"user.")
    for obj in contents.get("groups",{}).values():
        clear(obj,AUTO_GROUP_FIELDS,"group.")
    digest,count = manual_memory_digest(root)
    summary = {"manual_memories_preserved":count,"automatic_flags_disabled":len(changed_flags),
               "automatic_collections_cleared":len(cleared),"self_learning_removed":False,"applied":False}
    if not apply:
        return summary
    if is_running():
        raise RuntimeError("先通过服务管理器停止AstrBot，再清理记忆插件")
    backup = root / "data/astrbot/memory-cleanup-backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    _assert_managed_path(root,backup)
    backup.mkdir(parents=True)
    shutil.copy2(config,backup/config.name)
    shutil.copy2(store,backup/store.name)
    moved = []
    try:
        _write_json_atomically(config,settings)
        _write_store(store,contents)
        for index,path in enumerate(removals):
            if path.exists():
                destination = backup / (str(index)+"-"+path.name)
                _assert_managed_path(root,destination)
                # Both resolved endpoints have been checked inside this workspace.
                shutil.move(str(path),str(destination))
                moved.append((path,destination))
        if manual_memory_digest(root)[0] != digest:
            raise RuntimeError("手动持久记忆发生变化，停止清理")
    except Exception:
        shutil.copy2(backup/config.name,config)
        shutil.copy2(backup/store.name,store)
        for path,destination in reversed(moved):
            shutil.move(str(destination),str(path))
        raise
    summary.update(applied=True,self_learning_removed=not removals[0].exists(),backup_dir=str(backup))
    _write_json_atomically(backup/"summary.json",summary)
    return summary
