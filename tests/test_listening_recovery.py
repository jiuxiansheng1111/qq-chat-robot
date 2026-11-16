import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from test_listening_integration import event

from app import astrbot_listening as listening
from app import astrbot_soundtry as soundtry
from app.services.listening_memory import ListeningMemory
from app.services.listening_operations import ListeningOperations, RestoredListeningEvent

OWNER=("qq-local:GroupMessage:12345","23456")


def fixture(tmp_path):
    operations=ListeningOperations(tmp_path/"ops.json",clock=lambda:1000)
    operations.save_room(OWNER,"real-room",expires_at=8200,song_id="123",seen_songs=["123"])
    jobs=soundtry.SoundtryJobs(operations)
    manager=listening.MusicListening(jobs,ListeningMemory(tmp_path/"music.db"),operations=operations)
    e=event("一起听歌",user=OWNER[1],scope=OWNER[0])
    context=NS(get_platform_inst=lambda _:NS(meta=lambda:NS(name="aiocqhttp")),send_message=AsyncMock(return_value=True))
    return operations,jobs,manager,e,context


def test_operation_metadata_whitelist_expiry_and_corrupt_input_fail_closed(tmp_path):
    path=tmp_path/"ops.json"
    path.write_text(json.dumps({"version":1,"room":{"owner":OWNER,"room_id":"real-room","expires_at":8200,
        "text":"private words must not persist","token":"secret must not persist"},"analysis":[]}),encoding="utf-8")
    store=ListeningOperations(path,clock=lambda:1000)
    store.start("abc123abc123",OWNER)
    raw=path.read_text(encoding="utf-8")
    assert "private words" not in raw and "secret must" not in raw and "token" not in raw
    expired=ListeningOperations(path,clock=lambda:23000)
    assert expired.room() is None
    expired.purge()
    assert json.loads(path.read_text(encoding="utf-8"))=={"version":1,"room":None,"analysis":[]}
    path.write_text('{"version":1,"room":',encoding="utf-8")
    damaged=ListeningOperations(path)
    assert damaged.corrupt and damaged.room() is None and not damaged.interrupted()


def test_transient_windows_lock_does_not_lose_complete_operation_snapshot(monkeypatch,tmp_path):
    from app.services import listening_operations
    replace=listening_operations.os.replace
    calls=[0]
    def locked_once(source,target):
        calls[0]+=1
        if calls[0]==1:raise PermissionError("temporary scanner lock")
        return replace(source,target)
    monkeypatch.setattr(listening_operations.os,"replace",locked_once)
    store=ListeningOperations(tmp_path/"ops.json",clock=lambda:1000)
    assert store.save_room(OWNER,"real-room",expires_at=8200)
    assert ListeningOperations(store.path,clock=lambda:1000).room()["owner"]==list(OWNER)


@pytest.mark.asyncio
async def test_restore_confirms_same_live_room_without_accept_and_retries_interrupted_current_song(monkeypatch,tmp_path):
    store,_jobs,manager,_e,context=fixture(tmp_path)
    store.start("abc123abc123",OWNER,room_id="real-room",song_id="123")
    store.update("abc123abc123","interrupted")
    api=AsyncMock(return_value={"in_room":True,"room_id":"real-room","song_id":"123"})
    monkeypatch.setattr(listening,"listen_together",api)
    monkeypatch.setattr(manager,"_room",AsyncMock())
    report=await manager.restore(NS(),context,owner_check=lambda owner:True)
    assert report["room_restored"] and not report["accept_called"]
    assert api.await_count==1 and len(api.await_args.args)==1
    assert manager.room_owner==OWNER and "123" not in manager.room_seen
    assert not store.interrupted() and context.send_message.await_count==0
    await manager.close(preserve=True)
    assert store.room()["room_id"]=="real-room"


@pytest.mark.asyncio
async def test_restore_never_joins_or_moves_to_another_actual_room(monkeypatch,tmp_path):
    store,_jobs,manager,_e,context=fixture(tmp_path)
    api=AsyncMock(return_value={"in_room":True,"room_id":"other-room","song_id":"456"})
    monkeypatch.setattr(listening,"listen_together",api)
    report=await manager.restore(NS(),context)
    assert report["status"]=="previous_room_ended" and manager.room_task is None
    assert store.room() is None and api.await_count==1
    context.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_leave_during_restore_invalidates_late_confirmation(monkeypatch,tmp_path):
    store,_jobs,manager,e,context=fixture(tmp_path)
    entered,release=asyncio.Event(),asyncio.Event()
    async def status(*args):
        entered.set();await release.wait()
        return {"in_room":True,"room_id":"real-room","song_id":"123"}
    monkeypatch.setattr(listening,"listen_together",status)
    restoring=asyncio.create_task(manager.restore(NS(),context))
    await asyncio.wait_for(entered.wait(),1)
    assert (await manager.call(e,NS(),context,action="leave"))["status"]=="stopped"
    release.set()
    assert (await restoring)["status"]=="cancelled"
    assert store.room() is None and manager.room_task is None


@pytest.mark.asyncio
async def test_shutdown_marks_interrupted_job_but_explicit_leave_cannot_recreate_room_state(monkeypatch,tmp_path):
    store,jobs,manager,e,context=fixture(tmp_path)
    entered=asyncio.Event()
    async def loader():
        entered.set();await asyncio.Event().wait()
    manager.room_owner=OWNER;manager.room_id="real-room";manager.room_expires_at=8200
    def terminal(outcome):manager._checkpoint_room()
    jobs.start_source(e,NS(),context,loader=loader,room_id="real-room",song_id="123",on_terminal=terminal)
    await asyncio.wait_for(entered.wait(),1)
    await manager.close()
    assert store.room() is None and not store.interrupted() and not jobs.jobs
    e.send.assert_not_awaited()
    jobs.start_source(e,NS(),context,loader=loader)
    await asyncio.sleep(0)
    await jobs.close(preserve=True)
    assert len(store.interrupted())==1 and store.interrupted()[0]["status"]=="interrupted"


@pytest.mark.asyncio
async def test_interruption_notice_only_original_owner_scope_once_after_success(monkeypatch,tmp_path):
    store,_jobs,manager,_e,context=fixture(tmp_path)
    store.clear_room()
    store.start("abc123abc123",OWNER)
    store.start("abc123abc124",(OWNER[0],"34567"))
    monkeypatch.setattr(RestoredListeningEvent,"plain_result",lambda self,text:text)
    report=await manager.restore(NS(),context,owner_check=lambda owner:owner[1]==OWNER[1])
    assert report["interruption_notices"]==1 and not store.interrupted()
    assert context.send_message.await_args.args[0]==OWNER[0]
    second=listening.MusicListening(soundtry.SoundtryJobs(),operations=store)
    await second.restore(NS(),context,owner_check=lambda _:True)
    assert context.send_message.await_count==1


@pytest.mark.asyncio
async def test_failed_recovery_delivery_keeps_operation_for_later_confirmation(monkeypatch,tmp_path):
    store,_jobs,manager,_e,context=fixture(tmp_path)
    store.clear_room();store.start("abc123abc123",OWNER)
    context.send_message.return_value=False
    monkeypatch.setattr(RestoredListeningEvent,"plain_result",lambda self,text:text)
    report=await manager.restore(NS(),context,owner_check=lambda _:True)
    assert report["interruption_notices"]==0 and len(store.interrupted())==1


@pytest.mark.asyncio
async def test_track_changes_are_queued_while_audio_slot_is_busy(monkeypatch,tmp_path):
    store,jobs,manager,e,context=fixture(tmp_path)
    manager.room_owner=OWNER;manager.room_id="real-room";manager.room_expires_at=8200
    song=lambda value:{"in_room":True,"room_id":"real-room","song_id":value,"play_status":"PLAY"}
    api=AsyncMock(side_effect=[song("1"),{"success":True},song("2"),{"success":True},song("2"),{"success":True},{"in_room":False}])
    monkeypatch.setattr(listening,"listen_together",api)
    monkeypatch.setattr(listening.asyncio,"sleep",AsyncMock())
    start=Mock(side_effect=[json.dumps({"status":"busy"}),json.dumps({"status":"started"}),json.dumps({"status":"started"})])
    monkeypatch.setattr(jobs,"start_source",start)
    await manager._room(e,NS(),context,{"room_id":"real-room"})
    assert [call.kwargs["song_id"] for call in start.call_args_list]==["1","1","2"]
    assert e.send.await_count==1 and store.room() is None


@pytest.mark.asyncio
async def test_transient_status_failure_recovers_without_accept_or_extra_qq_notice(monkeypatch,tmp_path):
    _store,_jobs,manager,e,context=fixture(tmp_path)
    manager.room_owner=OWNER;manager.room_id="real-room";manager.room_expires_at=8200
    manager.room_seen={"123"}
    api=AsyncMock(side_effect=[TimeoutError(),{"in_room":True,"room_id":"real-room","song_id":"123"},{"success":True},{"in_room":False}])
    monkeypatch.setattr(listening,"listen_together",api)
    monkeypatch.setattr(listening.asyncio,"sleep",AsyncMock())
    await manager._room(e,NS(),context,{"room_id":"real-room"})
    assert api.await_count==4 and e.send.await_count==1
    assert all(len(call.args)==1 or call.args[1]=="heartbeat" for call in api.await_args_list)


@pytest.mark.asyncio
async def test_reconnected_room_chat_keeps_cursor_instead_of_skipping_backlog(monkeypatch,tmp_path):
    store,_jobs,manager,e,context=fixture(tmp_path)
    manager.room_owner=OWNER;manager.room_id="real-room";manager.room_expires_at=8200
    manager.room_task=NS(done=lambda:False)
    manager.room_chat_initialized=True;manager.room_chat_cursor=7
    calls=[]
    async def chat(settings,action,**kwargs):
        calls.append((action,kwargs))
        if action=="connect":return {"connected":True,"success":True,"cursor":99}
        if action=="messages":raise asyncio.CancelledError()
        return {"success":True}
    monkeypatch.setattr(listening,"room_chat",chat)
    with pytest.raises(asyncio.CancelledError):await manager._room_chat(e,NS(),context,"real-room")
    assert next(kwargs["cursor"] for action,kwargs in calls if action=="messages")==7
    assert store.room()["chat_cursor"]==7


@pytest.mark.asyncio
async def test_non_owner_gets_temporary_analysis_without_long_term_music_or_archive(monkeypatch,tmp_path):
    audio=tmp_path/"audio.wav";audio.write_bytes(b"fixture")
    e=event()
    memory=NS(remember=Mock(),recall=Mock(return_value=[{"title":"private old memory"}]))
    monkeypatch.setattr(soundtry,"is_owner_event",lambda _:False)
    monkeypatch.setattr(soundtry,"analyze_file",AsyncMock(return_value={"report":"实际音乐","data":{"heard":True}}))
    provider=NS(text_chat=AsyncMock(return_value=NS(completion_text="这段旋律挺放松")))
    jobs=soundtry.SoundtryJobs();jobs.on_delivered=AsyncMock()
    context=NS(get_using_provider_async=AsyncMock(return_value=provider))
    jobs.start_source(e,NS(),context,loader=AsyncMock(return_value=(audio,{"song_id":"123"})),memory=memory)
    await asyncio.gather(*list(jobs.jobs.values()))
    assert e.send.await_count==1
    memory.remember.assert_not_called();memory.recall.assert_not_called();jobs.on_delivered.assert_not_awaited()
    manager=listening.MusicListening(jobs,memory)
    manager.prepare(e,NS(system_prompt="人格"))
    memory.recall.assert_not_called()


@pytest.mark.asyncio
async def test_owner_analysis_archive_hook_runs_after_confirmed_final_delivery(monkeypatch,tmp_path):
    audio=tmp_path/"audio.wav";audio.write_bytes(b"fixture")
    e=event(user=OWNER[1],scope=OWNER[0])
    monkeypatch.setattr(soundtry,"is_owner_event",lambda _:True)
    monkeypatch.setattr(soundtry,"load_owner_policy",lambda:NS(music_profile_enabled=True))
    monkeypatch.setattr(soundtry,"analyze_file",AsyncMock(return_value={"report":"实际音乐","data":{"heard":True}}))
    provider=NS(text_chat=AsyncMock(return_value=NS(completion_text="完整听感")))
    jobs=soundtry.SoundtryJobs()
    async def archive(event,text,source):
        assert e.send.await_count==1 and text=="完整听感" and source=="music_analysis"
    jobs.on_delivered=AsyncMock(side_effect=archive)
    memory=ListeningMemory(tmp_path/"music.db")
    jobs.start_source(e,NS(),NS(get_using_provider_async=AsyncMock(return_value=provider)),
        loader=AsyncMock(return_value=(audio,{"song_id":"123"})),memory=memory)
    await asyncio.gather(*list(jobs.jobs.values()))
    assert jobs.on_delivered.await_count==1 and memory.recall(jobs.owner(e))


@pytest.mark.asyncio
async def test_plugin_loaded_checks_recovery_without_blocking_and_shutdown_preserves_operations(monkeypatch):
    from test_astrbot_plugin import _load_plugin
    module=_load_plugin(monkeypatch)
    context=NS()
    plugin=module.QQChatPlugin(context,{"legacy_chat":False})
    settings=NS()
    runtime=NS(_core=NS(settings=settings),close=AsyncMock())
    plugin.runtime=runtime
    manager=NS(restore=AsyncMock(return_value={"status":"checked","room_restored":False}),close=AsyncMock())
    jobs=NS(close=AsyncMock())
    plugin._music_listening=manager;plugin._soundtry_jobs=jobs
    monkeypatch.setattr(plugin,"_get_music_listening",lambda:manager)
    monkeypatch.setattr(plugin,"_prepare_local_menus",AsyncMock())
    await plugin.prepare_menus_after_start()
    await plugin._music_restore_task
    manager.restore.assert_awaited_once_with(settings,context,route_allowed=plugin._legacy_event_allowed)
    await plugin.terminate()
    manager.close.assert_awaited_once_with(preserve=True)
    jobs.close.assert_awaited_once_with(preserve=True)
    assert plugin.runtime is None and plugin._music_restore_task is None


@pytest.mark.asyncio
async def test_disabled_group_or_blocked_sender_cannot_restore_old_room_or_deliver_notices(monkeypatch,tmp_path):
    store,_jobs,manager,_e,context=fixture(tmp_path)
    store.start("abc123abc123",OWNER)
    api=AsyncMock()
    monkeypatch.setattr(listening,"listen_together",api)
    report=await manager.restore(NS(),context,owner_check=lambda _:True,route_allowed=AsyncMock(return_value=False))
    assert report["status"]=="route_denied" and store.room() is None and not store.interrupted()
    api.assert_not_awaited();context.send_message.assert_not_awaited()


def test_private_operation_cannot_point_at_another_persons_session(tmp_path):
    store=ListeningOperations(tmp_path/"ops.json",clock=lambda:1000)
    wrong=("qq-local:FriendMessage:34567","23456")
    assert not store.start("abc123abc123",wrong)
    assert not store.save_room(wrong,"real-room",expires_at=8200)
    assert not store.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("observed,valid",[(b"119.8",True),(b"30",False),(b"nan",False)])
async def test_complete_song_requires_actual_duration_match_not_just_enough_bytes(monkeypatch,observed,valid):
    from app import astrbot_video
    monkeypatch.setattr(astrbot_video,"_binary",lambda *args:"ffprobe-fixture")
    monkeypatch.setattr(astrbot_video,"_run",AsyncMock(return_value=observed))
    if valid:
        assert await listening.complete_song_duration("fixture.wav",120,NS())==119.8
    else:
        with pytest.raises(ValueError,match="尚未作为完整曲目"):
            await listening.complete_song_duration("fixture.wav",120,NS())


@pytest.mark.asyncio
@pytest.mark.parametrize("data,valid",[({"ok":True,"duration":120},True),({"ok":False},False),([],False)])
async def test_analyzer_explicit_failure_or_invalid_evidence_never_becomes_completed_listen(monkeypatch,data,valid):
    process=NS(returncode=0,communicate=AsyncMock(return_value=(b"audio report\n@@JSON@@"+json.dumps(data).encode(),b"")))
    monkeypatch.setattr(soundtry.asyncio,"create_subprocess_exec",AsyncMock(return_value=process))
    monkeypatch.setattr(soundtry,"_binary",lambda *args:"ffmpeg-fixture")
    if valid:
        assert (await soundtry._analyze_file("fixture.wav","read",NS(singing_python="python-fixture")))["data"]["ok"]
    else:
        with pytest.raises(ValueError,match="未确认"):
            await soundtry._analyze_file("fixture.wav","read",NS(singing_python="python-fixture"))


@pytest.mark.asyncio
async def test_shutdown_after_send_ack_before_archive_completion_does_not_mark_delivery_unknown(monkeypatch,tmp_path):
    store=ListeningOperations(tmp_path/"ops.json",clock=lambda:1000)
    jobs=soundtry.SoundtryJobs(store)
    e=event(user=OWNER[1],scope=OWNER[0])
    audio=tmp_path/"audio.wav";audio.write_bytes(b"fixture")
    monkeypatch.setattr(soundtry,"is_owner_event",lambda _:True)
    monkeypatch.setattr(soundtry,"analyze_file",AsyncMock(return_value={"report":"完整音轨","data":{"ok":True}}))
    provider=NS(text_chat=AsyncMock(return_value=NS(completion_text="完整听感")))
    entered=asyncio.Event()
    async def archive(*args,**kwargs):
        entered.set();await asyncio.Event().wait()
    jobs.on_delivered=archive
    outcomes=[]
    jobs.start_source(e,NS(),NS(get_using_provider_async=AsyncMock(return_value=provider)),
        loader=AsyncMock(return_value=(audio,{})),on_terminal=outcomes.append)
    await asyncio.wait_for(entered.wait(),1)
    assert e.send.await_count==1 and not store.interrupted()
    await jobs.close(preserve=True)
    assert not store.interrupted() and outcomes==["completed"]
