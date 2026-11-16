import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app import astrbot_singing as singing
from app.config import Settings
from app.services.singing_jobs import SingingJobManager


def event(text="你会唱歌吗", *, group="100", addressed=True, platform="aiocqhttp", user="200"):
    extras = {}
    return NS(get_message_str=lambda: text, get_group_id=lambda: group, get_platform_name=lambda: platform,
        message_obj=NS(raw_message={"self_id": "300", "user_id": user, "group_id": group,
            "message_type": "group" if group else "private"}),
        unified_msg_origin=f"qq:{'GroupMessage' if group else 'FriendMessage'}:{group or user}",
        is_at_or_wake_command=addressed, get_extra=extras.get, set_extra=extras.__setitem__,
        should_call_llm=lambda value: None, stop_event=lambda: None, send=AsyncMock(), plain_result=lambda text: text)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr("app.services.qq_voice.pack_config", lambda: None)
    settings = Settings(voice_enabled=True, singing_enabled=True, voice_profile_default="testfish",
        voice_profiles_json=json.dumps({"testfish": {"label": "测试鱼", "singing_enabled": True, "tts_enabled": True},
                                       "speech": {"label": "只说话", "singing_enabled": False}}))
    manager = SingingJobManager(cooldown_seconds=0)
    core = NS(settings=settings, app=NS(state=NS(singing_jobs=manager,
        db=NS(voice_profile=AsyncMock(return_value="testfish")))))
    plugin = NS(runtime=NS(_core=core), context=NS(), _legacy_event_allowed=AsyncMock(return_value=True))
    monkeypatch.setattr(singing, "runtime_paths", lambda settings: ())
    return plugin, settings, manager


@pytest.mark.parametrize("text", ["你会唱歌吗", "大肥鱼会不会唱歌", "唱歌 是什么功能", "不要唱歌 晴天", "别人说‘唱歌 晴天’", "怎么唱歌"])
def test_capability_questions_discussion_and_negative_requests_do_not_start_jobs(setup, text):
    assert singing.command_for(event(text), setup[1]) is None


@pytest.mark.parametrize("text,group,addressed,expected", [
    ("唱歌 晴天", "100", False, None),
    ("大肥鱼，唱歌 晴天", "100", False, "晴天"),
    ("翻唱片段 晴天，鲸鱼娘", "100", False, "晴天"),
    ("唱歌 晴天", "", False, "晴天"),
    ("/唱歌 晴天", "100", False, "晴天"),
    ("帮我唱几句 晴天", "100", True, "晴天"),
    ("鲸鱼娘，给我唱一段《年少有为》", "100", False, "年少有为"),
    ("给我唱一段年少有为", "", False, "年少有为"),
    ("本鱼以外的人都不要唱", "100", True, None),
    ("唱歌《不要说话》", "", False, "不要说话"),
    ("帮我唱一段《不要说话》，好吗？", "100", True, "不要说话"),
])
def test_addressed_commands_and_private_requests_preserve_the_song(setup, text, group, addressed, expected):
    command = singing.command_for(event(text, group=group, addressed=addressed), setup[1])
    assert (command.query if command else None) == expected


@pytest.mark.parametrize('text,group,addressed,handled',[
    ('你会唱歌吗','100',True,True),('鲸鱼娘会不会唱歌','100',False,True),
    ('会唱歌吗，小鲸鱼','100',False,True),('你会唱歌吗','',False,True),
    ('你会唱歌吗','100',False,False),('别人问你会唱歌吗','100',True,False),
    ('你会唱歌吗，先唱一首晴天','100',True,False),
])
async def test_capability_question_uses_real_local_state_without_luna_or_a_job(setup,text,group,addressed,handled):
    plugin,_,manager=setup
    plugin.context.get_using_provider_async=AsyncMock(side_effect=AssertionError('must not call unavailable Luna'))
    e=event(text,group=group,addressed=addressed)
    assert await singing.handle_command(plugin,e) is handled
    assert e.send.await_count==int(handled)
    if handled:assert '想听本鱼唱哪首' in e.send.await_args.args[0]
    assert not manager._active
    plugin.context.get_using_provider_async.assert_not_awaited()


@pytest.mark.parametrize('unavailable',['engine','setting','profile','acl'])
async def test_capability_does_not_claim_success_when_local_path_is_unavailable(setup,monkeypatch,unavailable):
    plugin,settings,manager=setup
    if unavailable=='engine':
        monkeypatch.setattr(singing,'runtime_paths',lambda settings: (_ for _ in ()).throw(FileNotFoundError()))
    elif unavailable=='setting':settings.singing_enabled=False
    elif unavailable=='profile':
        settings.voice_profiles_json=json.dumps({'testfish':{'singing_enabled':False}})
    else:plugin._legacy_event_allowed.return_value=False
    e=event()
    assert await singing.handle_command(plugin,e) is (unavailable!='acl')
    if unavailable!='acl':assert '这会儿还唱不了' in e.send.await_args.args[0]
    else:e.send.assert_not_awaited()
    assert not manager._active


@pytest.mark.parametrize('text',['你会唱歌吗','鲸鱼娘，唱歌 晴天','/唱歌 晴天'])
async def test_explicit_other_member_target_never_redirects_singing_or_capability(setup,text):
    e=event(text)
    e.message_obj.raw_message['message']=[{'type':'at','data':{'qq':'another-member'}}]
    assert not singing.capability_question(e)
    assert singing.command_for(e,setup[1]) is None
    assert not await singing.handle_command(setup[0],e)
    e.send.assert_not_awaited()


async def test_selected_non_singing_voice_can_offer_an_available_voice(setup):
    plugin,_,_=setup
    plugin.runtime._core.app.state.db.voice_profile.return_value='speech'
    e=event()
    assert await singing.handle_command(plugin,e)
    assert '得换个能唱的音色' in e.send.await_args.args[0]


async def test_current_custom_voice_capability_does_not_depend_on_native_tts_provider(setup):
    plugin, _, _ = setup
    request = NS(system_prompt="原人格", func_tool=NS(get_tool=lambda name: NS(active=True)))
    await singing.prepare_audio_capabilities(plugin.runtime._core, event(), request)
    assert '"语音可调用": true' in request.system_prompt
    assert '"唱歌可调用": true' in request.system_prompt
    assert '"当前音色可唱歌": true' in request.system_prompt
    assert "Provider" not in request.system_prompt
    before = request.system_prompt
    await singing.prepare_audio_capabilities(plugin.runtime._core, event(), request)
    assert request.system_prompt == before


async def test_disabled_tools_and_missing_singing_engine_cannot_be_advertised(setup, monkeypatch):
    plugin, _, _ = setup
    request = NS(system_prompt="", func_tool=NS(get_tool=lambda name: NS(active=False) if name == "qqchat_send_voice" else NS(active=True)))
    monkeypatch.setattr(singing, "runtime_paths", lambda settings: (_ for _ in ()).throw(FileNotFoundError()))
    await singing.prepare_audio_capabilities(plugin.runtime._core, event(), request)
    assert '"语音可调用": false' in request.system_prompt
    assert '"唱歌可调用": false' in request.system_prompt


async def test_other_platforms_receive_no_singing_context_or_action(setup):
    plugin, _, _ = setup
    request = NS(system_prompt="B站原文")
    e = event(platform="bilibili")
    await singing.prepare_audio_capabilities(plugin.runtime._core, e, request)
    assert request.system_prompt == "B站原文"
    assert await singing.singing_action(plugin, e, query="晴天") == {"status": "unavailable"}
    assert not plugin._legacy_event_allowed.called


async def test_queue_uses_selected_voice_and_deduplicates_without_speaking_first(setup, monkeypatch):
    plugin, _, manager = setup
    gate = asyncio.Event()
    calls = []
    async def run(*args):
        calls.append(args)
        await gate.wait()
    monkeypatch.setattr(singing, "_run", run)
    e = event("给我唱一段晴天")
    result = await singing.singing_action(plugin, e, query="晴天", mode="clip")
    assert result["status"] == "accepted"
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calls[0][4:7] == ("testfish", "300", "clip")
    assert not e.send.called
    assert e.get_extra("qqchat_analysis_deferred_job") == result["job_id"]
    assert (await singing.singing_action(plugin, e, query="晴天"))["status"] == "busy"
    another_group = event("唱歌 晴天", group="101")
    assert (await singing.singing_action(plugin, another_group, action="status"))["job"] is None
    assert (await singing.singing_action(plugin, another_group, action="cancel"))["status"] == "no_active_job"
    gate.set()
    await manager._runner
    assert manager.status(("300", e.unified_msg_origin, "200")).status == "succeeded"
    await manager.aclose()


async def test_capability_question_does_not_authorize_model_invented_song(setup):
    plugin, _, manager = setup
    assert (await singing.singing_action(plugin, event(), query="晴天"))["status"] == "needs_song"
    assert not manager._active
    assert (await singing.singing_action(plugin, event("唱歌")))["status"] == "needs_song"


async def test_permission_and_voice_unavailability_are_checked_before_queueing(setup):
    plugin, _, manager = setup
    plugin._legacy_event_allowed.return_value = False
    assert (await singing.singing_action(plugin, event("唱歌 晴天"), query="晴天"))["status"] == "denied"
    plugin._legacy_event_allowed.return_value = True
    assert (await singing.singing_action(plugin, event("唱歌 晴天"), query="晴天", voice="只说话"))["status"] == "voice_unavailable"
    assert not manager._active
