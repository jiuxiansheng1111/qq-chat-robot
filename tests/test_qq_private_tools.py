import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import main as bot
from app.astrbot_runtime import ChatRuntime
from app.services.music import NeteaseTrack
from app.services.music_workflow import MusicWorkflow
from app.services.translation import TranslationResult


def event(text, user="11", mid="1"):
    return {"post_type": "message", "message_type": "private", "self_id": "900", "user_id": user,
            "message_id": mid, "message": [{"type": "text", "data": {"text": text}}]}


@pytest.fixture
def runtime(tmp_path):
    seen = set()
    async def first(key):
        if key in seen:
            return False
        seen.add(key)
        return True

    limiter = SimpleNamespace(allow=AsyncMock(return_value=True))
    state = SimpleNamespace(db=SimpleNamespace(is_blocked=AsyncMock(return_value=False)),
                            deduplicator=SimpleNamespace(first_seen=first), music_workflow=MusicWorkflow(bot.settings),
                            ingress_limiter=limiter, music_action_limiter=limiter)
    rt = ChatRuntime(tmp_path, legacy_chat=False)
    rt._core = SimpleNamespace(app=SimpleNamespace(state=state), dispatch_onebot_event=bot.dispatch_onebot_event)
    rt._initialized = True
    return rt


async def test_translation_origin_survives_same_account_overlap(runtime, monkeypatch):
    ready, resume = asyncio.Event(), asyncio.Event()
    async def translate(_request, query, *, purpose):
        if query == "hello":
            ready.set()
            await resume.wait()
        return TranslationResult("你好" if query == "hello" else "再见", "", ())

    monkeypatch.setattr(bot, "cached_translation", translate)
    left, right = AsyncMock(return_value={"message_id": 1}), AsyncMock(return_value={"message_id": 2})
    original = event("大肥鱼，帮我翻译一下 hello")
    task = asyncio.create_task(runtime.handle_event(original, left))
    await asyncio.wait_for(ready.wait(), 5)
    other = await runtime.handle_event(event("/翻译 goodbye", user="22"), right)
    resume.set()
    first = await task
    assert first["reply_sent"] and other["reply_sent"] and first["source"] == other["source"] == "translation"
    for delivery, user, translation in [(left, "11", "你好"), (right, "22", "再见")]:
        delivery.assert_awaited_once()
        assert delivery.await_args.args == ("send_private_msg",)
        assert delivery.await_args.kwargs["user_id"] == user and "group_id" not in delivery.await_args.kwargs
        assert translation in delivery.await_args.kwargs["message"][0]["data"]["text"]
    duplicate = await runtime.handle_event(original, left)
    assert duplicate["reason"] == "duplicate_event" and not duplicate["reply_sent"]
    left.assert_awaited_once()
    for text in ("我今天买了一个翻译器", "/bot off", "选1"):
        assert not (await runtime.handle_event(event(text, mid="5"), left))["handled"]
    runtime._core.app.state.db.is_blocked.return_value = True
    assert (await runtime.handle_event(event("翻译 hello", mid="6"), left))["reason"] == "user_blocked"


async def test_music_private_search_pick_lyrics_and_cancel(runtime, monkeypatch):
    tracks = [NeteaseTrack(str(i), f"测试歌曲{i}", ("测试歌手",), "", "", 60) for i in (1, 2)]
    monkeypatch.setattr("app.services.music_workflow.search_music_candidates", AsyncMock(return_value=tracks))
    monkeypatch.setattr("app.services.music_workflow.fetch_music_covers", AsyncMock(return_value={}))
    monkeypatch.setattr("app.services.music_workflow.render_music_candidates", AsyncMock(return_value="base64://test"))
    monkeypatch.setattr("app.services.music_workflow.fetch_music_lyrics", AsyncMock(return_value="测试歌词"))
    monkeypatch.setattr("app.services.music_workflow.render_music_lyrics", AsyncMock(return_value=["base64://lyrics"]))
    api = AsyncMock(return_value={"message_id": 1})
    async def send(text, ident, user="11"):
        return await runtime.handle_event(event(text, user, ident), api)
    assert (await send("点歌 测试", "1"))["reply_sent"]
    assert not (await send("选1", "2", "22"))["handled"]
    assert (await send("选2", "3"))["reply_sent"]
    assert (await send("歌词2", "4"))["reply_sent"]
    assert (await send("取消", "5"))["reply_sent"]
    assert not (await send("选1", "6"))["handled"]
    segments = [seg for call in api.await_args_list for seg in call.kwargs["message"]]
    assert {seg["type"] for seg in segments} == {"image", "text", "music"}
    assert {"type": "music", "data": {"type": "163", "id": "2"}} in segments
    assert {"type": "image", "data": {"file": "base64://lyrics"}} in segments
    assert all(call.args == ("send_private_msg",) and call.kwargs["user_id"] == "11" for call in api.await_args_list)
