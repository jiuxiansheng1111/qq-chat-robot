"""Retired expression settings must not return through fallback plugin readers."""

import json
from pathlib import Path

import pytest

from app.astrbot_identity import QQ_PERSONA_PATH, persona_expression_settings
from scripts.configure_qq_feature_ownership import configure


def test_qq_companion_syncs_single_source_and_stops_duplicate_tone(tmp_path):
    data = tmp_path / "data/astrbot/instance/data"
    config = data / "config/astrbot_plugin_private_companion_config.json"
    schema = data / "plugins/astrbot_plugin_private_companion/_conf_schema.json"
    prompt = tmp_path / "prompts/qq/dafeiyu.txt"
    for path in (config, schema, prompt):
        path.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_bytes(QQ_PERSONA_PATH.read_bytes())
    expected = persona_expression_settings(prompt)
    fields = {key: {"type": "text", "default": ""} for key in expected}
    fields.update(enable_group_persona_denoise={"type": "bool", "default": True},
                  bot_scope_mode={"type": "string", "default": "all"},
                  bot_scope_ids={"type": "list", "default": []},
                  bot_name={"type": "string", "default": ""})
    schema.write_text(json.dumps({"basic_config": {"items": fields}}), encoding="utf-8")
    old = {key: "旧龙女仆20字限制" for key in expected}
    original = {**old, "basic_config": {**old, "enable_group_persona_denoise": True},
                "media_settings": {"voice": "preserve", "image": "preserve"}}
    config.write_text(json.dumps(original), encoding="utf-8")
    before = config.read_bytes()
    assert not configure(tmp_path)["applied"]
    with pytest.raises(RuntimeError, match="停止"):
        configure(tmp_path, apply=True, is_running=lambda: True)
    assert config.read_bytes() == before
    result = configure(tmp_path, apply=True, is_running=lambda: False)
    assert result["applied"]
    current = json.loads(config.read_text(encoding="utf-8-sig"))
    for key, value in expected.items():
        assert current[key] == current["basic_config"][key] == value
    assert current["basic_config"]["enable_group_persona_denoise"] is False
    assert current["basic_config"]["bot_scope_ids"] == ["qq-chatrobot-local"]
    assert current["media_settings"] == original["media_settings"]
    assert Path(result["backup_dir"]).joinpath(config.name).read_bytes() == before
    assert not configure(tmp_path, apply=True, is_running=lambda: False)["applied"]

    # Editing the one source updates both grouped and compatibility readers.
    prompt.write_text(prompt.read_text(encoding="utf-8").replace("- 性格标签：", "- 性格标签：新偏好、"), encoding="utf-8")
    assert configure(tmp_path, apply=True, is_running=lambda: False)["applied"]
    changed = json.loads(config.read_text(encoding="utf-8-sig"))
    assert changed["default_style"] == changed["basic_config"]["default_style"] == "新偏好、" + expected["default_style"]
