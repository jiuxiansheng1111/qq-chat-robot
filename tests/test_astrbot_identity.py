"""身份替换只覆盖真人角色身份提问，技术配置与其他聊天不改写。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from test_astrbot_reply_guard import Event
from test_astrbot_reply_repair import response

from app.astrbot_config_persona import PERSONA_ID
from app.astrbot_identity import (
    CHARACTER_INTRODUCTION,
    COMPANION_VOICE_PROMPT,
    IDENTITY_STYLE_PROMPT,
    apply_character_identity,
    identity_question_kind,
    identity_request_prompt,
    persona_expression_settings,
)
from app.astrbot_reply_guard import GroupReplyGuard
from scripts.sync_astrbot_identity import sync_identity


@pytest.mark.parametrize("text", ["你是谁", "你叫什么名字呀？", "小丛雨，介绍一下你自己", "自我介绍一下",
                                  "你是什么模型", "你用的是什么模型？", "你是不是豆包扮演的", "你不是豆包吗",
                                  "你是谁做的", "你是谁写的", "你是真人吗", "你是AI吗"])
def test_general_identity_uses_character(text):
    assert identity_question_kind(text) == "character"


@pytest.mark.parametrize("text", ["你实际用的模型是什么", "你的底层是什么模型", "完整型号给我", "你由谁开发",
                                  "你是哪个公司开发的", "你的API供应商是哪家", "实际模型版本号", "别用角色名，报后台配置"])
def test_explicit_technical_configuration_remains_truthful(text):
    assert identity_question_kind(text) == "technical"
    prompt = identity_request_prompt(text, model_name="known-configured-model")
    assert "known-configured-model" in prompt and "不能冒充其他型号" in prompt


@pytest.mark.parametrize("text", ["你知道我是谁吗", "智谱这个公司怎么样", "豆包好用吗", "他说你是谁是什么意思",
                                  "不要自我介绍", "解释‘你是谁’", "你觉得这个模型怎么部署", "你是谁，先总结这个视频"])
def test_discussion_and_unrelated_current_questions_are_not_replaced(text):
    kind = identity_question_kind(text)
    assert kind != "character"


def identity_event(prompt="你是谁", *, group="1000000003"):
    event = Event(prompt=prompt, group=group)
    event.message_obj.raw_message["user_id"] = "511169313"
    return event


def test_identity_works_in_other_already_enabled_qq_group_and_survives_guard():
    guard = GroupReplyGuard()
    event, result = identity_event(), response("我是某个底层模型开发的助手")
    assert apply_character_identity(event, result, guard)
    assert result.completion_text == CHARACTER_INTRODUCTION
    event.result.chain[0].text = result.completion_text
    assert guard.prepare_result(event) is None
    event._has_send_oper = True
    guard.mark_sent(event)
    another, result = identity_event(), response("旧答")
    assert apply_character_identity(another, result, guard)
    another.result.chain[0].text = result.completion_text
    assert guard.prepare_result(another) is None


def test_natural_truthful_self_introductions_are_preserved():
    cases = [
        ("你是谁", "我是ChatGPT的龙女仆呀，平时叫我小龙就行。"),
        ("你是AI吗", "我是ChatGPT的龙女仆，是AI，平常聊天不用太拘谨。"),
        ("你是真人吗", "不是现实中的真人，我是ChatGPT的龙女仆，是AI。"),
    ]
    for prompt, answer in cases:
        event, result = identity_event(prompt), response(answer)
        assert apply_character_identity(event, result)
        assert result.completion_text == answer


def test_wrong_or_untruthful_identity_answers_are_corrected():
    cases = [
        ("你是谁", "我是智谱开发的小丛雨。", CHARACTER_INTRODUCTION),
        ("你是谁", "我是ChatGPT的龙女仆，还是个真人。", CHARACTER_INTRODUCTION),
        ("你是AI吗", "我是ChatGPT的龙女仆，但我不是AI。", "我是ChatGPT的龙女仆，是AI。"),
        ("你是真人吗", "当然，我是真人，也是ChatGPT的龙女仆。", "不是，我是ChatGPT的龙女仆，是AI。"),
    ]
    for prompt, answer, expected in cases:
        event, result = identity_event(prompt), response(answer)
        assert apply_character_identity(event, result)
        assert result.completion_text == expected


def test_private_identity_and_real_human_boundary():
    event = identity_event("你是真人吗")
    event.message_obj.raw_message["message_type"] = "private"
    result = response()
    assert apply_character_identity(event, result)
    assert "ChatGPT的龙女仆" in result.completion_text
    assert result.completion_text == "不是，我是ChatGPT的龙女仆，是AI。"


def test_explicit_ai_identity_is_answered_truthfully():
    event = identity_event("你是AI吗")
    result = response()
    assert identity_question_kind("你是AI吗") == "character"
    assert apply_character_identity(event, result)
    assert result.completion_text == "我是ChatGPT的龙女仆，是AI。"


@pytest.mark.parametrize("kind", ["technical", "command", "media", "tool", "bot", "self", "chunk", "other_question"])
def test_other_results_are_untouched(kind):
    event, result = identity_event(), response("原始输出")
    if kind == "technical":
        event.prompt = "你由谁开发"
        event.message_obj.raw_message["message"][0]["data"]["text"] = event.prompt
    elif kind == "command":
        event.extras["qqchat_natural_command"] = "群分析"
    elif kind == "media":
        from types import SimpleNamespace
        result.result_chain = SimpleNamespace(chain=[SimpleNamespace(type="Image")])
    elif kind == "tool":
        result.tools_call_name = ["some_tool"]
    elif kind == "bot":
        event.message_obj.raw_message["sender"] = {"is_bot": True}
    elif kind == "self":
        event.message_obj.raw_message["user_id"] = "3503565007"
    elif kind == "chunk":
        result.is_chunk = True
    elif kind == "other_question":
        event.message_obj.raw_message["message"][0]["data"]["text"] = "这个API怎么使用"
    assert not apply_character_identity(event, result)
    assert result.completion_text == "原始输出"


def test_no_guessed_technical_model_and_shared_casual_style_contract():
    prompt = identity_request_prompt("你的底层实际模型是什么")
    assert "未提供已经核实" in prompt
    assert "自然随和、轻松调侃" in IDENTITY_STYLE_PROMPT
    assert "不使用 emoji" in IDENTITY_STYLE_PROMPT
    assert "普通闲聊单次回复不超过20个字符（含标点）" in IDENTITY_STYLE_PROMPT
    assert "如实说明自己是AI、不是现实中的真人" in IDENTITY_STYLE_PROMPT
    assert "嘴臭毒舌" not in IDENTITY_STYLE_PROMPT and "先怼两句" not in IDENTITY_STYLE_PROMPT
    assert CHARACTER_INTRODUCTION == "我是ChatGPT的龙女仆。"
    assert "gpt-6-luna" not in CHARACTER_INTRODUCTION
    from app.astrbot_reply_repair import _SYSTEM
    assert IDENTITY_STYLE_PROMPT in _SYSTEM
    assert identity_question_kind("这个API怎么使用") is None


@pytest.fixture
def identity_workspace(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/persona.txt").write_text(
        "【角色身份与技术配置】\n小丛雨的新身份规则\n\n"
        "【说话性格】\n- 性格标签：测试性格。\n按测试文件里的语气回答。\n\n"
        "【回复风格】\n测试回复规则。\n", encoding="utf-8")
    (tmp_path / "prompts/examples.jsonl").write_text(
        json.dumps({"messages": [{"role": "assistant", "content": "你好呀"}]}, ensure_ascii=False), encoding="utf-8")
    database = tmp_path / "data/astrbot/instance/data/data_v4.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE personas (persona_id TEXT PRIMARY KEY, system_prompt TEXT, begin_dialogs TEXT, tools TEXT)")
        connection.execute("INSERT INTO personas VALUES (?,?,?,?)", (PERSONA_ID, "old prompt", '["preserved dialogs"]', '["preserved tools"]'))
        connection.execute("INSERT INTO personas VALUES (?,?,?,?)", ("another-persona", "untouched", '[]', '[]'))
    config = database.parent / "config/astrbot_plugin_private_companion_config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"persona_conversation_voice_prompt": "old style", "enable": False,
                                  "model_id": "unchanged-model", "key": "synthetic-preserved-secret",
                                  "basic_config": {"persona_conversation_voice_prompt": "old nested", "enable_voice": True}}), encoding="utf-8")
    return tmp_path, database, config


def test_dry_run_never_changes_running_config_or_database(identity_workspace):
    root, db, config = identity_workspace
    old_config = config.read_bytes()
    result = sync_identity(root, is_running=lambda: True)
    assert not result["applied"] and config.read_bytes() == old_config
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT system_prompt FROM personas WHERE persona_id=?", (PERSONA_ID,)).fetchone()[0] == "old prompt"
    assert not (root / "data/astrbot/identity-backups").exists()


def test_apply_requires_stopped_service(identity_workspace):
    root, _, config = identity_workspace
    old = config.read_bytes()
    with pytest.raises(RuntimeError, match="停止"):
        sync_identity(root, dry_run=False, is_running=lambda: True)
    assert config.read_bytes() == old


def test_stop_sync_only_changes_owned_identity_and_both_voice_fields(identity_workspace):
    root, db, config = identity_workspace
    result = sync_identity(root, dry_run=False, is_running=lambda: False)
    assert result["applied"]
    cfg = json.loads(config.read_text(encoding="utf-8-sig"))
    expected = persona_expression_settings(root / "prompts/persona.txt")
    for key, value in expected.items():
        assert cfg[key] == value
        assert cfg["basic_config"][key] == value
    assert not cfg["enable"] and cfg["basic_config"]["enable_voice"]
    assert cfg["model_id"] == "unchanged-model" and cfg["key"] == "synthetic-preserved-secret"
    with sqlite3.connect(db) as connection:
        prompt, dialogs, tools = connection.execute("SELECT system_prompt,begin_dialogs,tools FROM personas WHERE persona_id=?", (PERSONA_ID,)).fetchone()
        assert "小丛雨的新身份规则" in prompt and "你好呀" in prompt
        assert dialogs == '["preserved dialogs"]' and tools == '["preserved tools"]'
        assert connection.execute("SELECT system_prompt FROM personas WHERE persona_id='another-persona'").fetchone()[0] == "untouched"
    backup = Path(result["backup_dir"])
    assert (backup / db.name).is_file() and (backup / config.name).is_file()
    assert not sync_identity(root, dry_run=False, is_running=lambda: False)["applied"]


def test_editing_only_persona_updates_all_expression_entries(identity_workspace):
    root, db, config = identity_workspace
    sync_identity(root, dry_run=False, is_running=lambda: False)
    path = root / "prompts/persona.txt"
    path.write_text(path.read_text(encoding="utf-8").replace("测试性格", "改后的性格")
                    .replace("测试回复规则", "新的回复规则"), encoding="utf-8")
    sync_identity(root, dry_run=False, is_running=lambda: False)
    cfg = json.loads(config.read_text(encoding="utf-8-sig"))
    for target in (cfg, cfg["basic_config"]):
        assert target["default_style"] == "改后的性格"
        assert "改后的性格" in target["persona_conversation_voice_prompt"]
        assert "新的回复规则" in target["reply_style_prompt"]
    with sqlite3.connect(db) as connection:
        prompt = connection.execute("SELECT system_prompt FROM personas WHERE persona_id=?", (PERSONA_ID,)).fetchone()[0]
        assert "改后的性格" in prompt and "新的回复规则" in prompt


def test_default_identity_and_companion_read_same_persona_source():
    settings = persona_expression_settings()
    assert IDENTITY_STYLE_PROMPT == COMPANION_VOICE_PROMPT == settings["persona_conversation_voice_prompt"]


def test_incomplete_persona_does_not_change_runtime(identity_workspace):
    root, db, config = identity_workspace
    before = config.read_bytes()
    (root / "prompts/persona.txt").write_text("还没写完的人格", encoding="utf-8")
    with pytest.raises(ValueError, match="缺少段落"):
        sync_identity(root, dry_run=False, is_running=lambda: False)
    assert config.read_bytes() == before
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT system_prompt FROM personas WHERE persona_id=?", (PERSONA_ID,)).fetchone()[0] == "old prompt"



