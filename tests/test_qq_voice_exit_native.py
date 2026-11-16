import importlib.util
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types, _make_group_event


@pytest.mark.parametrize("text,target,allowed,exit_expected", [
    ("退出语音模式", "bare", True, True),
    ("/退出语音模式", "self", True, True),
    ("关闭语音", "bare", True, True),
    ("恢复文字回复", "bare", True, True),
    ("大肥鱼，切回文字吧", "bare", True, True),
    ("退出语音模式", "private", True, True),
    ("退出语音模式", "other", True, False),
    ("退出语音模式", "bare", False, False),
    ("不要退出语音模式", "self", True, False),
    ("怎么关闭语音", "self", True, False),
    ("“关闭语音”", "self", True, False),
])
async def test_exit_is_local_single_text_scoped_and_persistent(monkeypatch, tmp_path, text, target, allowed, exit_expected):
    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "native"))
    types = _load_astrbot_types(root, monkeypatch)
    from app.config import Settings
    from app.db.database import Database
    settings = Settings(_env_file=None, voice_enabled=False, database_path=str(tmp_path / "voice.db"))
    db = Database(settings)
    await db.init()
    scope = "private:12345" if target == "private" else "12345"
    await db.set_voice_mode(scope, "12345", True)
    await db.set_voice_profile(scope, "12345", "original-voice")
    await db.set_voice_mode(scope, "other-user", True)
    await db.set_voice_mode("other-group", "12345", True)
    history = {(scope, "12345", "voice"): ["pending"], (scope, "other-user", "voice"): ["keep"]}
    spec = importlib.util.spec_from_file_location("_voice_exit_native_plugin", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    context = NS(get_using_provider_async=AsyncMock(side_effect=AssertionError("no model required")))
    plugin = module.QQChatPlugin(context, {"legacy_chat": False, "group_chat_groups": ["12345"]})
    plugin.runtime = NS(_core=NS(settings=settings, app=NS(state=NS(db=db, voice_conversation_history=history))))
    monkeypatch.setattr(plugin, "_legacy_event_allowed", AsyncMock(return_value=allowed))
    monkeypatch.setattr("app.astrbot_owner_memory.observe", AsyncMock())
    monkeypatch.setattr("app.astrbot_plugin_status.private_companion_photo_hint", lambda *args: None)
    monkeypatch.setattr("app.astrbot_singing.handle_command", AsyncMock(return_value=False))
    monkeypatch.setattr("app.astrbot_weather.handle_command", AsyncMock(return_value=False))
    monkeypatch.setattr(plugin, "_get_music_listening", lambda: NS(observe=lambda e: None))
    e, api, raw = _make_group_event(types)
    e.message_obj.group_id = "12345"
    e.message_str = text
    e.message_obj.message = [types.Plain(text)]
    raw["message"] = [{"type": "text", "data": {"text": text}}]
    if target in {"self", "other"}:
        recipient = "99999" if target == "self" else "other-member"
        raw["message"].insert(0, {"type": "at", "data": {"qq": recipient}})
        e.message_obj.message.insert(0, types.At(qq=recipient))
    if target == "private":
        e.message_obj.type = types.MessageType.FRIEND_MESSAGE
        e.message_obj.group_id = ""
        raw["message_type"] = "private"
        raw.pop("group_id", None)
    else:
        plugin._get_group_router().prepare_event(e)
    async def send(**params):
        return await api.call_action("send_private_msg" if target == "private" else "send_group_msg", **params)
    e.bot.send_group_msg = send
    e.bot.send_private_msg = send
    await plugin.route_native_group_message(e)
    # Reopen the database: this must survive service restart.
    reopened = Database(settings)
    assert await reopened.voice_mode(scope, "12345") is not exit_expected
    assert await reopened.voice_mode(scope, "other-user")
    assert await reopened.voice_mode("other-group", "12345")
    assert await reopened.voice_profile(scope, "12345") == "original-voice"
    assert len(api.calls) == int(exit_expected)
    context.get_using_provider_async.assert_not_awaited()
    if exit_expected:
        assert e.is_stopped()
        assert api.calls[0][1]["message"] == [{"type": "text", "data": {"text": "好的，我知道了"}}]
        assert (scope, "12345", "voice") not in history
    assert (scope, "other-user", "voice") in history
