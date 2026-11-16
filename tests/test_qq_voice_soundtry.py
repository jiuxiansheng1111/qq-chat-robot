import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_astrbot_group_chat import Event

from app import astrbot_soundtry as soundtry
from app.astrbot_group_chat import GroupChatRouter, normalize_natural_command


@pytest.mark.parametrize("text,expected", [("大肥鱼，帮我看看这个",True), ("帮我看看这个，大肥鱼",True),
    ("帮我总结这个视频",False), ("怎么配队？",False), ("音频分析这段音乐",False)])
def test_only_addressed_media_requests_wake_without_passive_cooldown(text,expected):
    config = {"group_chat_enabled":True,"group_chat_groups":["1000000001"],"group_chat_bot_names":["大肥鱼"],
              "group_chat_trigger_keywords":["音频分析"],"group_chat_help_requests":True,
              "group_chat_unaddressed_questions":True,"group_chat_min_interval_seconds":600,
              "group_chat_addressed_only":True}
    router = GroupChatRouter(config,clock=lambda:1000)
    event = Event(text,message_id="new")
    state = router._state(event)
    state.last_reply_at, state.passive_unanswered = 999, True
    reason=router.observe_event(event)
    assert (reason in {"name","direct"}) is expected
    assert event.is_at_or_wake_command is expected


@pytest.mark.parametrize(("text","expected"), [("帮我选择音色 芳乃，大肥鱼","选择音色 芳乃"),
                                                   ("帮我点歌 晴天","点歌 晴天"),
                                                   ("翻唱 芳乃 晴天","翻唱 芳乃 晴天"),
                                                   ("唱歌 是什么功能",None),("不要点歌 晴天",None)])
def test_local_function_requests_keep_existing_command_interfaces(text, expected):
    assert normalize_natural_command(text,names=["大肥鱼"]) == expected


def test_qq_persona_is_identical_for_all_sound_profiles():
    from app.main import conversation_identity
    assert conversation_identity("mako",{}) == conversation_identity("yoshino",{}) == conversation_identity("murasame",{})
    assert conversation_identity("mako",{})[1] == "大肥鱼"


@pytest.mark.asyncio
async def test_listening_job_keeps_chat_available_and_follows_up_with_actual_result(monkeypatch,tmp_path):
    extras = {}
    event = NS(unified_msg_origin="qq:group:fixture",message_obj=NS(raw_message={"user_id":"u"}),
               get_extra=lambda k,d=None: extras.get(k,d),set_extra=lambda k,v:extras.update({k:v}),
               get_platform_name=lambda:"aiocqhttp",get_message_str=lambda:"大肥鱼一起听这首歌",
               plain_result=lambda text:text,send=AsyncMock())
    extras[soundtry._ATTACHMENTS] = [object()]
    entered,release = asyncio.Event(),asyncio.Event()
    async def analyze(*args):
        entered.set()
        await release.wait()
        return {"report":"真实分析：节奏约120BPM","data":{"bpm":120}}
    audio=tmp_path/"music.wav"
    audio.write_bytes(b"fixture audio")
    monkeypatch.setattr(soundtry,"attachment_path",AsyncMock(return_value=audio))
    from unittest.mock import Mock

    from app.services.listening_memory import ListeningMemory
    monkeypatch.setattr(ListeningMemory,"remember",Mock())
    monkeypatch.setattr(ListeningMemory,"recall",Mock(return_value=[]))
    monkeypatch.setattr(soundtry,"analyze_file",analyze)
    provider=NS(text_chat=AsyncMock(return_value=NS(completion_text="节奏挺稳，想聊点什么")))
    context=NS(get_using_provider_async=AsyncMock(return_value=provider))
    jobs=soundtry.SoundtryJobs()
    assert json.loads(jobs.start(event,NS(),context))["status"] == "started"
    await asyncio.wait_for(entered.wait(),1)
    # A new ordinary chat request can proceed while the inference is waiting.
    next_request=NS(system_prompt="大肥鱼聊天")
    soundtry.prepare_soundtry(event,next_request,jobs)
    assert "大肥鱼聊天" in next_request.system_prompt
    assert not event.send.called
    release.set()
    await asyncio.gather(*list(jobs.jobs.values()))
    assert event.send.await_args.args[0] == "节奏挺稳，想聊点什么"
    summary_args=provider.text_chat.await_args.kwargs
    assert "真实分析" in summary_args["prompt"] and "角色名称：大肥鱼" in summary_args["system_prompt"]
    soundtry.prepare_soundtry(event,next_request,jobs)
    assert "真实分析" in next_request.system_prompt
    await jobs.close()
