"""Keep one QQ chat pipeline and retain the distinct, user-requested tools."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.astrbot_identity import persona_expression_settings
from scripts.cleanup_qq_memory import AUTO_FLAGS, _write_store
from scripts.configure_astrbot import _assert_managed_path, _write_json_atomically
from scripts.sync_astrbot_identity import _running

# The QQ plugin owns conversation entry, persona, contextual replies and media
# tools. Companion remains installed as the UI and Image Companion host.
OFF_BY_CATEGORY = {
    "duplicate_chat": {
        "enable_multi_persona_mode", "enable_persona_voice_channels",
        "enable_group_bot_name_wakeup", "enable_group_wakeup_question",
        "enable_group_wakeup_enhancement", "enable_group_conversation_followup",
        "enable_group_interjection", "enable_group_repeat_follow",
        "enable_message_debounce", "enable_smart_message_debounce",
        "enable_group_context_injection", "enable_group_history_injection",
        "intercept_astrbot_group_context", "enable_response_self_review",
        "enable_passive_response_review", "enable_proactive_message_review",
        "enable_smart_silence", "enable_passive_topic_suppression",
        "enable_proactive_chat_integration",
        "enable_model_perception",
        "enable_group_persona_denoise",
    },
    "automatic_memory": set(AUTO_FLAGS) | {
        "enable_relationship_analysis", "enable_relationship_state_machine",
        "enable_open_loop_tracking", "enable_intent_emotion_analysis",
        "enable_custom_relationship_stage_policy",
        "enable_emotion_simulation", "enable_llm_emotion_judgement",
        "enable_relationship_violation_penalties",
    },
    "simulated_offline_activity": {
        "enable_daily_plan", "include_schedule_in_messages",
        "enable_humanized_states", "enable_health_state", "enable_hunger_state",
        "enable_cycle_state", "inject_passive_states",
        "enable_passive_state_delta_injection", "enable_rest_reply_simulation",
        "enable_busy_reply_gate",
    },
    "background_browsing": {
        "enable_bilibili_integration", "enable_bilibili_boredom_watch",
        "enable_news_integration", "enable_news_boredom_read",
        "enable_ai_daily_watch", "enable_web_exploration",
        "enable_web_exploration_boredom_search", "enable_hot_trend_sources",
        "enable_news_daily_hot_read",
    },
    "unsolicited_account_actions": {
        "default_proactive_enabled", "enable_qzone_integration",
        "enable_qzone_life_publish", "enable_qzone_generated_image_publish",
        "enable_qzone_comment_inbox", "enable_qzone_emotional_vent_publish",
        "enable_qq_presence_sync", "enable_qq_custom_presence_sync",
        "enable_atrelay_tools",
    },
}


def _schema_leaves(schema: dict):
    for group, spec in schema.items():
        for key, leaf in spec.get("items", {}).items():
            yield group, key, leaf


def configure(root: Path, *, apply=False, is_running=_running) -> dict:
    root = root.resolve(strict=True)
    data = root / "data/astrbot/instance/data"
    path = data / "config/astrbot_plugin_private_companion_config.json"
    schema_path = data / "plugins/astrbot_plugin_private_companion/_conf_schema.json"
    for target in (path, schema_path):
        _assert_managed_path(root, target)
    original = json.loads(path.read_text(encoding="utf-8-sig"))
    value = copy.deepcopy(original)
    schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
    original_schema = copy.deepcopy(schema)
    # AstrBot drops keys absent from the schema on every load. Without this
    # hidden marker declaration Companion mistakes each restart for its first
    # upgrade and forcibly turns the relationship system back on.
    schema.setdefault("_relationship_switch_semantics_version", {
        "type": "int", "default": 0, "invisible": True,
        "description": "Internal relationship switch migration version",
    })
    leaves = {key: (group, leaf) for group, key, leaf in _schema_leaves(schema)}
    changes = []
    missing = []
    desired = {key: False for keys in OFF_BY_CATEGORY.values() for key in keys}
    # Only this adapter is within the QQ change scope. Background services and
    # future adapters must not accidentally inherit Companion chat hooks.
    desired.update(bot_scope_mode="allowlist", bot_scope_ids=["qq-chatrobot-local"], bot_name="大肥鱼")
    # This plugin is scoped to QQ above. Keep its fallback/proactive readers on
    # the same source too, instead of leaving retired styles behind the filter.
    desired.update(persona_expression_settings(root / "prompts/qq/dafeiyu.txt"))
    for key, target in sorted(desired.items()):
        spec = leaves.get(key)
        if spec is None:
            missing.append(key)
            continue
        group, leaf = spec
        if isinstance(target, bool) and leaf.get("type") != "bool":
            raise ValueError(f"Unexpected schema type for {key}")
        current = value.setdefault(group, {})
        old = current.get(key, leaf.get("default"))
        if old != target:
            changes.append({"key": key, "group": group, "before": old, "after": target})
        current[key] = copy.deepcopy(target)
        # AstrBot can hydrate these retired aliases again at startup; grouped
        # values are authoritative. Keep existing aliases consistent on write.
        if key in value:
            value[key] = copy.deepcopy(target)
    # Companion 6.0.7 repurposed this switch and its first-run migration forces
    # old values on. This is a new explicit disable under the new semantics,
    # so acknowledge that migration instead of letting startup undo the choice.
    if "enable_custom_relationship_stage_policy" in leaves:
        value["_relationship_switch_semantics_version"] = 1
    report = {
        "applied": False,
        "changes": changes,
        "off_by_category": {name: sorted(keys & leaves.keys()) for name, keys in OFF_BY_CATEGORY.items()},
        "missing_schema_keys": sorted(missing),
        "effective_expected": {key: target for key, target in desired.items() if key in leaves},
        "scope": "qq-chatrobot-local",
        "schema_marker_fixed": schema != original_schema,
        "retained": ["native_luna_chat", "native_group_participation", "proactive_group_chat",
                     "manual_and_music_memory", "web_search", "voice_and_singing", "music_listening",
                     "sound_and_video", "mahjong", "image_companion_host", "meme_manager_assets",
                     "group_reports", "manual_pixiv", "lifesim"],
    }
    if not apply or (value == original and schema == original_schema):
        return report
    if is_running():
        raise RuntimeError("先通过服务管理器停止 AstrBot，再更新功能开关")
    backup = root / "data/astrbot/config-backups" / (
        "qq-feature-ownership-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    _assert_managed_path(root, backup)
    backup.mkdir(parents=True)
    shutil.copy2(path, backup / path.name)
    shutil.copy2(schema_path, backup / schema_path.name)
    try:
        if schema != original_schema:
            _write_store(schema_path, schema)
        _write_json_atomically(path, value)
    except Exception:
        shutil.copy2(backup / path.name, path)
        shutil.copy2(backup / schema_path.name, schema_path)
        raise
    return {**report, "applied": True, "backup_dir": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(configure(ROOT, apply=args.apply), ensure_ascii=False))
