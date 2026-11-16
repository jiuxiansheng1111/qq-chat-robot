import json

import pytest

from app.astrbot_group_chat import GROUP_PARTICIPATION_DEFAULTS
from scripts.configure_qq_participation import configure


def test_participation_updates_only_qq_chat_and_qq_idle_prompts(tmp_path):
    data = tmp_path / "data/astrbot/instance/data/config"
    data.mkdir(parents=True)
    chat = data / "astrbot_plugin_qq_chatrobot_config.json"
    chat.write_text(json.dumps({"group_chat_groups": ["12345"], "group_chat_min_interval_seconds": 600,
                                "legacy_chat": False}), encoding="utf-8")
    idle = data / "astrbot_plugin_proactive_chat_config.json"
    idle.write_text(json.dumps({"friend_settings": {"enable": False, "proactive_prompt": "保留私聊"},
                               "group_settings": {"session_list": ["qq-chatrobot-local:GroupMessage:12345"],
                                                  "proactive_prompt": "旧龙女仆20字符限制", "enable": True,
                                                  "schedule_settings": {"quiet_hours": "23-8"}},
                               "web_admin": {"password": "test-only"}}), encoding="utf-8")
    bili = data / "astrbot_plugin_bilibili_ai_bot_config.json"
    bili.write_text('{"model":"existing","persona":"existing"}', encoding="utf-8")
    bili_before = bili.read_bytes()
    original = chat.read_bytes()
    assert configure(tmp_path)["applied"] is False
    with pytest.raises(RuntimeError, match="停止"):
        configure(tmp_path, apply=True, is_running=lambda: True)
    assert chat.read_bytes() == original
    result = configure(tmp_path, apply=True, is_running=lambda: False)
    assert result["applied"] is True
    current = json.loads(chat.read_text(encoding="utf-8-sig"))
    assert all(current[key] == value for key, value in GROUP_PARTICIPATION_DEFAULTS.items())
    assert current["group_chat_groups"] == ["12345"] and current["legacy_chat"] is False
    new_idle = json.loads(idle.read_text(encoding="utf-8-sig"))
    assert "龙女仆" not in new_idle["group_settings"]["proactive_prompt"]
    assert "20字符" not in new_idle["group_settings"]["proactive_prompt"]
    assert new_idle["group_settings"]["schedule_settings"] == {"quiet_hours": "23-8"}
    assert new_idle["friend_settings"] == {"enable": False, "proactive_prompt": "保留私聊"}
    assert new_idle["web_admin"] == {"password": "test-only"}
    assert bili.read_bytes() == bili_before
    assert configure(tmp_path, apply=True, is_running=lambda: False)["applied"] is False


def test_no_configured_groups_does_not_expand_scope(tmp_path):
    data = tmp_path / "data/astrbot/instance/data/config"
    data.mkdir(parents=True)
    chat = data / "astrbot_plugin_qq_chatrobot_config.json"
    chat.write_text('{"group_chat_groups":[]}', encoding="utf-8")
    before = chat.read_bytes()
    with pytest.raises(ValueError, match="不自动扩大"):
        configure(tmp_path, apply=True, is_running=lambda: False)
    assert chat.read_bytes() == before
