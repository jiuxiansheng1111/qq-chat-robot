import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_listening_integration import event

from app import astrbot_analysis_delivery as delivery
from app import astrbot_listening as listening
from app import astrbot_soundtry as soundtry
from app.astrbot_silence import NO_REPLY_MARKER
from app.services.listening_memory import ListeningMemory
from app.services.netease_member import NeteaseSelfInviteError


def receipt_event(text="听这首歌"):
    e=event(text)
    e.result=NS(is_llm_result=lambda:True)
    e.get_result=lambda:e.result
    e.clear_result=lambda:setattr(e,"result",None)
    e.should_call_llm=lambda value:setattr(e,"llm_allowed",value)
    e.stop_event=lambda:setattr(e,"stopped",True)
    return e


def test_only_accepted_analysis_initial_model_receipt_is_suppressed():
    e=receipt_event()
    delivery.defer_analysis_reply(e,"job-1")
    response=NS(completion_text="本鱼开始分析啦",reasoning_content="process")
    assert delivery.defer_model_receipt(e,response)
    assert response.completion_text==NO_REPLY_MARKER and response.completion_text
    assert delivery.suppress_model_receipt(e) and e.result is None and e.stopped
    normal=receipt_event("今天吃了什么")
    response=NS(completion_text="本鱼想喝点热汤")
    assert not delivery.defer_model_receipt(normal,response)
    assert not delivery.suppress_model_receipt(normal) and normal.result is not None


def test_tool_calls_and_unrelated_platforms_are_not_rewritten():
    e=receipt_event()
    delivery.defer_analysis_reply(e,"job-1")
    response=NS(completion_text="",tools_call_name=["qqchat_music_artist"])
    assert not delivery.defer_model_receipt(e,response)
    e.get_platform_name=lambda:"weixin_oc"
    assert not delivery.suppress_model_receipt(e)


def test_failed_join_control_notice_remains_visible_even_if_song_analysis_started():
    e=receipt_event()
    delivery.defer_analysis_reply(e,"job-1")
    delivery.keep_control_reply(e)
    response=NS(completion_text="同一个账号不能作为第二位成员加入")
    assert not delivery.defer_model_receipt(e,response)
    assert not delivery.suppress_model_receipt(e)


@pytest.mark.asyncio
async def test_accepted_job_sends_nothing_until_complete_then_one_full_result(monkeypatch,tmp_path):
    e=receipt_event()
    audio=tmp_path/"input.wav";audio.write_bytes(b"fixture")
    entered,release=asyncio.Event(),asyncio.Event()
    async def analyze(*args):
        entered.set()
        await release.wait()
        return {"report":"实际旋律资料","data":{"heard":True}}
    monkeypatch.setattr(soundtry,"analyze_file",analyze)
    answer="完整听感"+"旋律舒缓，节奏留出了呼吸的空间，"*240+"让人想起晴天"
    provider=NS(text_chat=AsyncMock(return_value=NS(completion_text=answer)))
    context=NS(get_using_provider_async=AsyncMock(return_value=provider))
    jobs=soundtry.SoundtryJobs()
    status=json.loads(jobs.start_source(e,NS(),context,loader=AsyncMock(return_value=(audio,{}))))
    assert status["status"]=="started" and status["reply_policy"]=="deferred_until_terminal"
    assert delivery.is_deferred_analysis_reply(e)
    await asyncio.wait_for(entered.wait(),1)
    await asyncio.sleep(0)
    e.send.assert_not_awaited()
    delivery.suppress_model_receipt(e)
    release.set()
    await asyncio.gather(*list(jobs.jobs.values()))
    assert e.send.await_count==1 and e.send.await_args.args[0]==answer
    assert len(answer)>3000 and not audio.exists()
    assert "不逐句播报" in provider.text_chat.await_args.kwargs["prompt"]


@pytest.mark.asyncio
async def test_analysis_failure_has_one_clear_terminal_result(monkeypatch,tmp_path):
    e=receipt_event()
    jobs=soundtry.SoundtryJobs()
    jobs.start_source(e,NS(),NS(),loader=AsyncMock(side_effect=ValueError("歌曲没有播放权限")))
    e.send.assert_not_awaited()
    await asyncio.gather(*list(jobs.jobs.values()))
    assert e.send.await_count==1 and "歌曲没有播放权限" in e.send.await_args.args[0]
    assert "任务已结束" in e.send.await_args.args[0]


@pytest.mark.asyncio
async def test_transport_failure_does_not_send_second_misleading_analysis_error(monkeypatch,tmp_path):
    e=receipt_event();e.send.side_effect=RuntimeError("send transport failed")
    audio=tmp_path/"input.wav";audio.write_bytes(b"fixture")
    monkeypatch.setattr(soundtry,"analyze_file",AsyncMock(return_value={"report":"实际已听完","data":{}}))
    provider=NS(text_chat=AsyncMock(return_value=NS(completion_text="完整听感")))
    jobs=soundtry.SoundtryJobs()
    jobs.start_source(e,NS(),NS(get_using_provider_async=AsyncMock(return_value=provider)),
        loader=AsyncMock(return_value=(audio,{})))
    outcomes=await asyncio.gather(*list(jobs.jobs.values()),return_exceptions=True)
    assert isinstance(outcomes[0],RuntimeError) and e.send.await_count==1
    assert e.send.await_args.args[0]=="完整听感"


@pytest.mark.asyncio
@pytest.mark.parametrize("song,paused,deferred",[("123",False,True),("123",True,False),("",False,False)])
async def test_confirmed_join_defers_receipt_only_when_current_song_will_be_analyzed(monkeypatch,tmp_path,song,paused,deferred):
    e=receipt_event("https://st.music.163.com/listen-together/share/?roomId=real&inviterId=456")
    manager=listening.MusicListening(soundtry.SoundtryJobs(),ListeningMemory(tmp_path/"music.db"))
    monkeypatch.setattr(listening,"listen_together",AsyncMock(side_effect=[{},
        {"in_room":True,"room_id":"real","song_id":song,"play_status":"PAUSE" if paused else "PLAY"}]))
    monkeypatch.setattr(manager,"_room",AsyncMock())
    result=await manager.call(e,NS(),NS(),action="join")
    assert result["status"]=="joined" and delivery.is_deferred_analysis_reply(e) is deferred
    await manager.close()


@pytest.mark.asyncio
async def test_missing_invite_and_same_account_failures_are_not_silenced(monkeypatch,tmp_path):
    manager=listening.MusicListening(soundtry.SoundtryJobs(),ListeningMemory(tmp_path/"music.db"))
    e=receipt_event("陪我听歌")
    result=await manager.call(e,NS(),NS(),action="join")
    assert result["status"]=="missing_invitation" and not delivery.is_deferred_analysis_reply(e)
    e=receipt_event("https://st.music.163.com/listen-together/share/?roomId=real&inviterId=456&songId=123")
    monkeypatch.setattr(listening,"listen_together",AsyncMock(side_effect=NeteaseSelfInviteError("same account")))
    monkeypatch.setattr(listening,"load_song",AsyncMock(side_effect=ValueError("unavailable")))
    result=await manager.call(e,NS(),NS(),action="join")
    assert result["status"]=="same_account" and not delivery.is_deferred_analysis_reply(e)
    await asyncio.gather(*list(manager.jobs.jobs.values()))


@pytest.mark.asyncio
async def test_missing_audio_attachment_does_not_defer_the_error():
    e=receipt_event()
    jobs=soundtry.SoundtryJobs()
    result=json.loads(jobs.start(e,NS(),NS()))
    assert result["status"]=="missing_attachment" and not delivery.is_deferred_analysis_reply(e)
