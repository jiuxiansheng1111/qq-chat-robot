import hashlib
import hmac

from fastapi.testclient import TestClient

from app.main import (
    CAT_IMAGE_COMMANDS,
    NAILONG_IMAGE_COMMANDS,
    PIG_IMAGE_COMMANDS,
    app,
    asks_for_sender_name,
    bot_mentioned,
    enforce_possession_identity,
    extract_group_memory,
    extract_group_memory_deletion,
    extract_long_memory,
    extract_music_query,
    extract_search_query,
    is_identity_question,
    mentioned_image_command,
    mentioned_user_ids,
    message_text,
    polish_chat_reply,
    sender_display_name,
    settings,
    webhook_token_valid,
)


def event(text: str, group_id: str = "integration-group", user_id: str = "member", role: str = "member"):
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": group_id,
        "user_id": user_id,
        "message": text,
        "sender": {"user_id": user_id, "role": role},
    }


def mention_event(text: str):
    payload = event(text)
    payload["message"] = [
        {"type": "at", "data": {"qq": ""}},
        {"type": "text", "data": {"text": text}},
    ]
    return payload


def post_event(client: TestClient, payload: dict):
    headers = {}
    if settings.onebot_webhook_token:
        headers["X-OneBot-Token"] = settings.onebot_webhook_token
    return client.post("/onebot/webhook", json=payload, headers=headers)


def test_at_message_is_detected():
    previous = settings.onebot_self_id
    settings.onebot_self_id = "bot-1"
    try:
        payload = event("你好")
        payload["message"] = [
            {"type": "at", "data": {"qq": "bot-1"}},
            {"type": "text", "data": {"text": "你好"}},
        ]
        assert bot_mentioned(payload)
        assert message_text(payload) == "你好"
    finally:
        settings.onebot_self_id = previous


def test_at_image_commands_are_detected_without_triggering_ai():
    previous = settings.onebot_self_id
    settings.onebot_self_id = "bot-1"
    try:
        cat = event("随机猫咪")
        cat["message"] = [
            {"type": "at", "data": {"qq": "bot-1"}},
            {"type": "text", "data": {"text": "随机猫咪"}},
        ]
        pig = event("随机猪猪")
        pig["message"] = [
            {"type": "at", "data": {"qq": "bot-1"}},
            {"type": "text", "data": {"text": "随机猪猪"}},
        ]
        nailong = event("随机奶龙")
        nailong["message"] = [
            {"type": "at", "data": {"qq": "bot-1"}},
            {"type": "text", "data": {"text": "随机奶龙"}},
        ]
        assert mentioned_image_command(cat, CAT_IMAGE_COMMANDS)
        assert mentioned_image_command(pig, PIG_IMAGE_COMMANDS)
        assert mentioned_image_command(nailong, NAILONG_IMAGE_COMMANDS)
        assert not mentioned_image_command(cat, PIG_IMAGE_COMMANDS)
    finally:
        settings.onebot_self_id = previous


def test_mention_extracts_search_and_explicit_long_memory():
    previous = settings.onebot_self_id
    settings.onebot_self_id = "bot-1"
    try:
        payload = event("搜索 Python 新版本")
        payload["message"] = [
            {"type": "at", "data": {"qq": "bot-1"}},
            {"type": "text", "data": {"text": "搜索 Python 新版本"}},
        ]
        assert extract_search_query(payload, message_text(payload)) == "Python 新版本"
        payload["message"][1]["data"]["text"] = "记住：我喜欢科幻"
        assert extract_long_memory(payload, message_text(payload)) == "我喜欢科幻"
    finally:
        settings.onebot_self_id = previous


def test_music_query_requires_slash_or_real_bot_mention():
    previous = settings.onebot_self_id
    settings.onebot_self_id = "bot-1"
    try:
        payload = event("点歌 ZUTOMAYO TAIDADA")
        assert extract_music_query(payload, message_text(payload)) is None
        payload["message"] = [
            {"type": "at", "data": {"qq": "bot-1"}},
            {"type": "text", "data": {"text": " 点歌 ZUTOMAYO TAIDADA"}},
        ]
        assert extract_music_query(payload, message_text(payload)) == "ZUTOMAYO TAIDADA"
        payload["message"][1]["data"]["text"] = "点歌TAIDADA"
        assert extract_music_query(payload, message_text(payload)) == "TAIDADA"
        payload["message"][1]["data"]["text"] = "点歌 taidada"
        assert extract_music_query(payload, message_text(payload)) == "taidada"
        assert extract_music_query(event("/点歌 Yorushika Sunny"), "/点歌 Yorushika Sunny") == (
            "Yorushika Sunny"
        )
    finally:
        settings.onebot_self_id = previous


def test_mention_position_does_not_change_array_command_text():
    previous = settings.onebot_self_id
    settings.onebot_self_id = "bot-1"
    try:
        before = event("随机猫咪")
        before["message"] = [
            {"type": "at", "data": {"qq": "bot-1"}},
            {"type": "text", "data": {"text": " 随机猫咪"}},
        ]
        after = event("随机猫咪")
        after["message"] = [
            {"type": "text", "data": {"text": "随机猫咪 "}},
            {"type": "at", "data": {"qq": "bot-1"}},
        ]
        assert bot_mentioned(before) and bot_mentioned(after)
        assert message_text(before) == message_text(after) == "随机猫咪"
        assert mentioned_image_command(before, CAT_IMAGE_COMMANDS)
        assert mentioned_image_command(after, CAT_IMAGE_COMMANDS)
    finally:
        settings.onebot_self_id = previous


def test_cq_string_mention_is_position_independent():
    previous = settings.onebot_self_id
    settings.onebot_self_id = "bot-1"
    try:
        before = event("unused")
        before["message"] = "[CQ:at,qq=bot-1] 随机奶龙"
        after = event("unused")
        after["message"] = "随机奶龙 [CQ:at,qq=bot-1]"
        assert bot_mentioned(before) and bot_mentioned(after)
        assert message_text(before) == message_text(after) == "随机奶龙"
        assert mentioned_image_command(before, NAILONG_IMAGE_COMMANDS)
        assert mentioned_image_command(after, NAILONG_IMAGE_COMMANDS)
    finally:
        settings.onebot_self_id = previous


def test_targeted_mention_returns_member_but_not_bot():
    previous = settings.onebot_self_id
    settings.onebot_self_id = "bot-1"
    try:
        payload = event("指向夺舍")
        payload["message"] = [
            {"type": "at", "data": {"qq": "member-2"}},
            {"type": "text", "data": {"text": " 指向夺舍 "}},
            {"type": "at", "data": {"qq": "bot-1"}},
        ]
        assert bot_mentioned(payload)
        assert message_text(payload) == "指向夺舍"
        assert mentioned_user_ids(payload) == ["member-2"]
    finally:
        settings.onebot_self_id = previous


def test_plain_possession_alias_with_target_is_a_targeted_command():
    from app.main import TARGETED_POSSESSION_COMMANDS

    previous = settings.onebot_self_id
    settings.onebot_self_id = "bot-1"
    try:
        payload = event("夺舍")
        payload["message"] = [
            {"type": "at", "data": {"qq": "bot-1"}},
            {"type": "text", "data": {"text": " 夺舍 "}},
            {"type": "at", "data": {"qq": "member-2"}},
        ]
        assert message_text(payload) in TARGETED_POSSESSION_COMMANDS
        assert mentioned_user_ids(payload) == ["member-2"]
    finally:
        settings.onebot_self_id = previous


def test_identity_questions_are_detected_without_llm_guessing():
    assert is_identity_question("你现在是谁？")
    assert is_identity_question("你还记得你是谁吗")
    assert is_identity_question("现在叫什么名字")
    assert not is_identity_question("今天天气怎么样")


def test_possession_identity_guard_replaces_default_name():
    assert enforce_possession_identity("我是阿柚，一个聊天机器人。", "山奈钠") == (
        "我是山奈钠，一个聊天机器人。"
    )


def test_chat_reply_uses_lele_wording():
    assert polish_chat_reply("他真是个乐子人。") == "他真是个乐乐。"


def test_sender_name_question_is_distinct_from_bot_identity():
    assert asks_for_sender_name("say my name")
    assert asks_for_sender_name("我叫什么名字？")
    assert not asks_for_sender_name("你叫什么名字？")


def test_comma_remember_command_creates_group_memory():
    previous = settings.onebot_self_id
    settings.onebot_self_id = "bot-1"
    try:
        payload = event("记住，hzh 是 Cat#")
        payload["message"] = [
            {"type": "at", "data": {"qq": "bot-1"}},
            {"type": "text", "data": {"text": " 记住，hzh 是 Cat#"}},
        ]
        assert extract_group_memory(payload, message_text(payload)) == "hzh 是 Cat#"
    finally:
        settings.onebot_self_id = previous


def test_group_memory_delete_command_extracts_literal_text():
    previous = settings.onebot_self_id
    settings.onebot_self_id = "bot-1"
    try:
        payload = event("删除hzh")
        payload["message"] = [
            {"type": "text", "data": {"text": "删除hzh "}},
            {"type": "at", "data": {"qq": "bot-1"}},
        ]
        assert extract_group_memory_deletion(payload, message_text(payload)) == "hzh"
    finally:
        settings.onebot_self_id = previous


def test_sender_display_name_prefers_group_card_and_sanitizes_lines():
    payload = event("hello")
    payload["sender"].update({"card": "小明\n第二行", "nickname": "nickname"})
    assert sender_display_name(payload) == "小明 第二行"


def test_webhook_token_accepts_custom_header_and_bearer_token():
    assert webhook_token_valid("secret", "secret", None)
    assert webhook_token_valid("secret", None, "Bearer secret")
    assert not webhook_token_valid("secret", None, "Bearer wrong")
    assert not webhook_token_valid("secret", None, None)
    assert webhook_token_valid("", None, None)


def test_webhook_token_accepts_napcat_hmac_signature():
    body = b'{"post_type":"meta_event"}'
    signature = "sha1=" + hmac.new(b"secret", body, hashlib.sha1).hexdigest()

    assert webhook_token_valid("secret", None, None, signature, body)
    assert not webhook_token_valid("secret", None, None, "sha1=wrong", body)
    assert not webhook_token_valid("secret", None, None, signature, b"tampered")


def test_group_lifecycle_and_blacklist(tmp_path):
    previous_database_path = settings.database_path
    previous_onebot_api_base = settings.onebot_api_base
    settings.database_path = str(tmp_path / "webhook.db")
    settings.onebot_api_base = ""
    with TestClient(app) as client:
        try:
            hello = event("/hello")
            hello["message_id"] = "integration-plugin-1"
            assert post_event(client, hello).json()["plugin"] == "hello"
            first = event("/help")
            first["message_id"] = "integration-message-1"
            assert post_event(client, first).json()["ok"]
            assert post_event(client, first).json()["reason"] == "duplicate_event"
            assert post_event(client, event("/bot off", role="admin", user_id="admin")).json()["ok"]
            assert post_event(client, event("/help")).json()["reason"] == "group_disabled"
            assert post_event(client, event("/bot on", role="admin", user_id="admin")).json()["ok"]
            assert post_event(client, event("/blacklist add blocked")).json()["ok"] is True
            assert post_event(client, event("/blacklist add blocked", role="admin", user_id="admin")).json()["ok"]
            assert post_event(client, event("/help", user_id="blocked")).json()["reason"] == "user_blocked"
        finally:
            settings.database_path = previous_database_path
            settings.onebot_api_base = previous_onebot_api_base
