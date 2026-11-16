import asyncio
import json
import sqlite3
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app import astrbot_guides as guides
from app import astrbot_listening as listening
from app import astrbot_soundtry as soundtry
from app.astrbot_video import VideoReadError, _parse_visual_summary
from app.services.listening_memory import ListeningMemory
from scripts.cleanup_qq_memory import cleanup, manual_memory_digest


def event(text="一起听", user="u", scope="qq:private:u"):
    extras={}
    return NS(unified_msg_origin=scope,message_obj=NS(raw_message={"user_id":user},message=[]),
              get_extra=lambda k,d=None:extras.get(k,d),set_extra=lambda k,v:extras.update({k:v}),
              get_platform_name=lambda:"aiocqhttp",get_message_str=lambda:text,
              plain_result=lambda t:t,send=AsyncMock())


def test_memory_persists_deduplicates_and_never_crosses_owners(tmp_path):
    path=tmp_path/"memory.db"
    memory=ListeningMemory(path)
    track={"song_id":"123","title":"春雷","artist":"米津玄師","aliases":["米津玄师"]}
    memory.remember(("room","u"),track,{"report":"实际节奏"},"节奏很灵巧&&happy&&")
    ListeningMemory(path).remember(("room","u"),track,{"report":"再次实际听取"},"今天更喜欢这种轻快感")
    rows=ListeningMemory(path).recall(("room","u"),"喜欢什么歌")
    assert len(rows)==1 and rows[0]["listen_count"]==2
    assert not memory.recall(("other","u")) and not memory.recall(("room","v"))
    with pytest.raises(ValueError):
        memory.remember(("room","u"),track,{},"假装听过")
    assert memory.forget(("room","v"))==0
    assert memory.forget(("room","u"),"123")==1


@pytest.mark.parametrize("content",[
    '说明\n```json\n{"observed":true,"summary":"先红后蓝","timeline":[{"time":0.00s}]}\n```',
    '{"observed":true,"summary":"先红后蓝","timeline":[]}\n补充说明',
])
def test_visual_evidence_parser_accepts_prose_and_literal_timestamp_repair(content):
    assert _parse_visual_summary(content)["summary"]=="先红后蓝"


@pytest.mark.parametrize("content",['{"observed":false,"summary":"猜测"}',
    '{"observed":true,"summary":"没有时间线","timeline":"invalid"}',
    '{"observed":true,"summary":"未完成',"只有标题"])
def test_visual_evidence_parser_rejects_unobserved_or_incomplete_content(content):
    with pytest.raises(VideoReadError):
        _parse_visual_summary(content)


@pytest.mark.asyncio
async def test_song_job_stores_only_actual_completed_audio_and_delivers_reply(monkeypatch,tmp_path):
    monkeypatch.setattr(soundtry,"music_memory_allowed",lambda e:True)
    audio=tmp_path/"input.wav"
    audio.write_bytes(b"fixture")
    metadata={"song_id":"123","title":"测试曲","artist":"测试歌手"}
    jobs=soundtry.SoundtryJobs()
    memory=ListeningMemory(tmp_path/"memory.db")
    provider=NS(text_chat=AsyncMock(return_value=NS(completion_text="这段旋律让我放松了些")))
    context=NS(get_using_provider_async=AsyncMock(return_value=provider))
    monkeypatch.setattr(soundtry,"analyze_file",AsyncMock(return_value={"report":"实际音轨旋律","data":{"heard":True}}))
    e=event()
    loader=AsyncMock(return_value=(audio,metadata))
    status=json.loads(jobs.start_source(e,NS(),context,loader=loader,memory=memory))
    assert status["status"]=="started"
    await asyncio.gather(*list(jobs.jobs.values()))
    assert "放松" in e.send.await_args.args[0]
    assert memory.recall(jobs.owner(e))[0]["song_id"]=="123"
    assert not audio.exists()
    e2=event(user="v")
    failed=AsyncMock(side_effect=ValueError("歌曲没有播放权限"))
    jobs.start_source(e2,NS(),context,loader=failed,memory=memory)
    await asyncio.gather(*list(jobs.jobs.values()))
    assert "没有播放权限" in e2.send.await_args.args[0]
    assert not memory.recall(jobs.owner(e2))


@pytest.mark.asyncio
async def test_join_uses_real_invitation_and_checks_confirmed_room(monkeypatch,tmp_path):
    e=event("来一起听 https://st.music.163.com/listen-together/share/?songId=123&roomId=test-room&inviterId=456")
    assert listening.invitation(e)=={"room_id":"test-room","inviter_id":"456","song_id":"123"}
    manager=listening.MusicListening(soundtry.SoundtryJobs(),ListeningMemory(tmp_path/"music.db"))
    api=AsyncMock(side_effect=[{"success":True},{"in_room":False}])
    monkeypatch.setattr(listening,"listen_together",api)
    result=await manager.call(e,NS(),NS(),action="join")
    assert result["status"]=="join_unconfirmed" and manager.room_task is None
    api.assert_awaited()
    result=await manager.call(event("你能加入房间吗"),NS(),NS(),query="invented",action="join")
    assert result["status"]=="missing_invitation" and api.await_count==2
    spoof=event("https://st.music.163.com.evil.example/listen-together/?roomId=x&inviterId=123")
    assert listening.invitation(spoof) is None


@pytest.mark.asyncio
async def test_confirmed_join_is_background_and_owner_can_stop_without_closing_other_room(monkeypatch,tmp_path):
    manager=listening.MusicListening(soundtry.SoundtryJobs(),ListeningMemory(tmp_path/"music.db"))
    e=event("一起听 https://st.music.163.com/listen-together/share/?roomId=real&inviterId=456")
    monkeypatch.setattr(listening,"listen_together",AsyncMock(side_effect=[{}, {"in_room":True,"room_id":"real"}]))
    entered=asyncio.Event()
    async def room(*args):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(manager,"_room",room)
    result=await manager.call(e,NS(),NS(),action="join")
    assert result["status"]=="joined"
    await asyncio.wait_for(entered.wait(),1)
    assert (await manager.call(event(user="v"),NS(),NS(),action="leave"))["status"]=="not_owner"
    assert (await manager.call(e,NS(),NS(),action="leave"))["status"]=="stopped"
    assert manager.room_task is None


def memory_fixture(root):
    data=root/"data/astrbot/instance/data"
    config=data/"config/astrbot_plugin_private_companion_config.json"
    store=data/"plugin_data/astrbot_plugin_private_companion/companions.json"
    config.parent.mkdir(parents=True)
    store.parent.mkdir(parents=True)
    config.write_text(json.dumps({"enable_companion_memory":True,"bilibili_account":"unchanged","enable_worldbook":True}))
    store.write_text(json.dumps({"users":{"u":{"companion_memory":{"items":[]},"memo_notes":["手动"]}},
                                 "groups":{"g":{"group_episodes":["自动"]}},"bot_diaries":["自动"]}))
    for directory in (data/"plugins/astrbot_plugin_self_learning",data/"plugin_data/astrbot_plugin_self_learning"):
        directory.mkdir(parents=True)
        (directory/"fixture.txt").write_text("自动学习")
    with sqlite3.connect(root/"data/qqchat.db") as db:
        db.execute("CREATE TABLE long_term_memories(id INTEGER,content TEXT)")
        db.execute("INSERT INTO long_term_memories VALUES(1,'手动持久记忆')")
    return config,store


def test_memory_cleanup_is_dry_by_default_and_preserves_explicit_records(tmp_path):
    config,store=memory_fixture(tmp_path)
    old=config.read_bytes(),store.read_bytes(),manual_memory_digest(tmp_path)
    assert not cleanup(tmp_path)["applied"]
    assert (config.read_bytes(),store.read_bytes(),manual_memory_digest(tmp_path))==old
    with pytest.raises(RuntimeError):
        cleanup(tmp_path,apply=True,is_running=lambda:True)
    result=cleanup(tmp_path,apply=True,is_running=lambda:False)
    assert result["self_learning_removed"]
    cfg=json.loads(config.read_text(encoding="utf-8-sig"))
    assert not cfg["enable_companion_memory"] and cfg["enable_worldbook"]
    assert cfg["bilibili_account"]=="unchanged"
    assert json.loads(store.read_text())["users"]["u"]["memo_notes"]==["手动"]
    assert manual_memory_digest(tmp_path)==old[2]


@pytest.mark.asyncio
async def test_familiar_guide_still_searches_every_turn(monkeypatch):
    search=AsyncMock(return_value='{"status":"ready","results":[]}')
    monkeypatch.setattr(guides,"native_web_search",search)
    monkeypatch.setattr(guides,"newest_bili_guides",AsyncMock(return_value=[]))
    monkeypatch.setattr(guides,"_private_query",lambda *a:False)
    for _ in range(2):
        e=event("熟悉的游戏也再查一次配队攻略")
        req=NS(system_prompt="大肥鱼")
        await guides.prepare_guide_context(NS(),e,req,NS())
        await guides.prepare_guide_context(NS(),e,req,NS())
        assert "已强制联网核对" in req.system_prompt
    assert search.await_count==2


@pytest.mark.parametrize("query,expected",[("https://music.163.com/#/song?id=123","123"),
    ("https://music.163.com/song?id=456","456"),("https://evil.example/song?id=456",""),
    ("../secrets","")])
def test_only_official_song_links_or_ids_are_resolved(query,expected):
    assert listening.song_id(query)==expected


def test_invitation_card_wakes_without_at_or_text():
    from test_astrbot_group_chat import Event

    from app.astrbot_group_chat import GroupChatRouter
    link="https://st.music.163.com/listen-together/share/?roomId=room&inviterId=123"
    e=Event("",components=[{"type":"json","data":{"meta":{"url":link}}}])
    router=GroupChatRouter({"group_chat_enabled":True,"group_chat_groups":["1000000001"],
        "group_chat_addressed_only":False})
    assert router.observe_event(e)=="keyword"
    assert e.is_at_or_wake_command


@pytest.mark.parametrize("text",[
    "陪我听歌", "陪我一起听歌", "大肥鱼陪我一起听歌", "鲸鱼娘，陪我听歌呀", "蠢鱼陪我听歌",
    "肥鱼陪我听歌", "陪我听歌，小鲸鱼", "能不能陪我听歌", "我们一起听歌吧",
])
def test_short_listening_invitation_wakes_without_at_keyword_or_passive_quota(text):
    from test_astrbot_group_chat import Event

    from app.astrbot_group_chat import GroupChatRouter

    assert listening.is_short_listening_request(text)
    router=GroupChatRouter({"group_chat_enabled":True,"group_chat_groups":["1000000001"],
        "group_chat_trigger_keywords":[],"group_chat_help_requests":False,
        "group_chat_unaddressed_questions":False,"group_chat_min_interval_seconds":600,
        "group_chat_addressed_only":False})
    e=Event(text)
    state=router._state(e)
    state.last_reply_at=router.clock()
    state.passive_unanswered=True
    expected="keyword" if text in {"陪我听歌","陪我一起听歌","能不能陪我听歌","我们一起听歌吧"} else "name"
    assert router.observe_event(e)==expected and e.is_at_or_wake_command


@pytest.mark.parametrize("text",[
    "不要陪我听歌", "他说陪我听歌", "“陪我听歌”", "陪我听歌是什么意思",
    "陪我听歌顺便查一下最新新闻", "肥鱼陪我听这首歌的创作背景",
])
def test_short_invitation_fast_path_excludes_negation_quotes_and_fact_questions(text):
    assert not listening.is_short_listening_request(text)


@pytest.mark.asyncio
async def test_missing_invitation_is_visible_control_reply_and_explicit_next_step(tmp_path):
    manager=listening.MusicListening(soundtry.SoundtryJobs(),ListeningMemory(tmp_path/"music.db"))
    e=event("陪我听歌")
    req=NS(system_prompt="大肥鱼")
    manager.prepare(e,req)
    assert "需要回应" in req.system_prompt and "不能把缺少参数当作无需回复" in req.system_prompt
    result=await manager.call(e,NS(),NS(),action="join")
    assert result["status"]=="missing_invitation" and "邀请链接" in result["message"]
    assert e.get_extra("qqchat_music_control_reply") is True
    assert manager.room_task is None and not manager.jobs.jobs


@pytest.mark.asyncio
async def test_same_account_does_not_claim_join_and_actual_audio_runs_in_background(monkeypatch,tmp_path):
    from app.services.netease_member import NeteaseSelfInviteError

    jobs=soundtry.SoundtryJobs()
    memory=ListeningMemory(tmp_path/"music.db")
    manager=listening.MusicListening(jobs,memory)
    e=event("一起听 https://st.music.163.com/listen-together/share/?roomId=real&inviterId=456&songId=123")
    api=AsyncMock(side_effect=NeteaseSelfInviteError("same account"))
    monkeypatch.setattr(listening,"listen_together",api)
    entered,release=asyncio.Event(),asyncio.Event()
    async def load(*args):
        entered.set()
        await release.wait()
        raise ValueError("音源暂时不可用")
    monkeypatch.setattr(listening,"load_song",load)
    result=await manager.call(e,NS(),NS(),action="join")
    assert result["status"]=="same_account" and result["joined"] is False
    assert result["audio"]["status"]=="started" and manager.room_task is None
    assert e.get_extra("qqchat_music_control_reply") is True
    await asyncio.wait_for(entered.wait(),1)
    # The room has not joined and a blocked audio loader does not block normal requests.
    req=NS(system_prompt="本轮继续聊天")
    manager.prepare(event("今天天气不错"),req)
    assert req.system_prompt=="本轮继续聊天"
    release.set()
    await asyncio.gather(*list(jobs.jobs.values()))
    assert "音源暂时不可用" in e.send.await_args.args[0]
    assert not memory.recall(jobs.owner(e))
    assert api.await_count==1


@pytest.mark.asyncio
async def test_same_account_without_song_does_not_start_audio_or_room(monkeypatch,tmp_path):
    from app.services.netease_member import NeteaseSelfInviteError

    manager=listening.MusicListening(soundtry.SoundtryJobs(),ListeningMemory(tmp_path/"music.db"))
    e=event("https://st.music.163.com/listen-together/share/?roomId=real&inviterId=456")
    monkeypatch.setattr(listening,"listen_together",AsyncMock(side_effect=NeteaseSelfInviteError("same account")))
    result=await manager.call(e,NS(),NS(),action="join")
    assert result["status"]=="same_account" and not result["joined"]
    assert result["audio"]["status"]=="missing_song" and "歌曲链接" in result["message"]
    assert manager.room_task is None and not manager.jobs.jobs


@pytest.mark.parametrize("component",[
    {"type":"json","data":{"data":json.dumps({"app":"com.tencent.tuwen.lua","meta":{"news":{"jumpUrl":"https://st.music.163.com/listen-together/share/index.html?roomId=test&inviterId=456&songId=123"}}})}},
    {"type":"xml","data":{"data":'<msg url="https://st.music.163.com/listen-together/share/index.html?roomId=test&amp;inviterId=456&amp;songId=123" />'}},
    {"type":"music","data":{"url":"https://music.163.com/song?id=123"}},
])
def test_bubble_cards_only_expose_actual_official_music_urls(component):
    e=event("")
    e.message_obj.message=[component]
    assert listening.music_card_source(e).startswith("https://")
    assert listening.song_id(listening.music_card_source(e))=="123"
    e.get_platform_name=lambda:"weixin_oc"
    assert listening.music_card_source(e)==""


def test_oversized_deep_or_spoofed_music_cards_do_not_create_invites():
    for value in ["https://st.music.163.com.evil.example/listen-together/?roomId=x&inviterId=456",
                  "x"*64001+"https://st.music.163.com/listen-together/?roomId=x&inviterId=456"]:
        e=event("");e.message_obj.message=[{"type":"json","data":{"data":value}}]
        assert not listening.music_card_source(e)
    cyclic={};cyclic['next']=cyclic
    e=event("");e.message_obj.message=[{"type":"json","data":cyclic}]
    assert not listening.music_card_source(e)


def test_card_invite_cache_is_short_lived_and_isolated_by_entire_conversation_and_user(monkeypatch,tmp_path):
    import sys
    from types import ModuleType

    from test_astrbot_group_chat import Event

    components=ModuleType("astrbot.api.message_components")
    components.Plain=lambda text:NS(type="plain",text=text)
    monkeypatch.setitem(sys.modules,components.__name__,components)
    now=[1000]
    manager=listening.MusicListening(soundtry.SoundtryJobs(),ListeningMemory(tmp_path/"music.db"),clock=lambda:now[0])
    link="https://st.music.163.com/listen-together/share/index.html?roomId=test&inviterId=456&songId=123"
    card=Event("",components=[{"type":"json","data":{"data":json.dumps({"meta":{"news":{"jumpUrl":link}}})}}])
    card.unified_msg_origin="qq-chatrobot-local:GroupMessage:group-a"
    assert manager.observe(card) and card.is_at_or_wake_command
    assert link in card.get_message_str() and len(card.message_obj.message)==2
    assert manager.observe(card) and len(card.message_obj.message)==2
    same=event("陪我听歌",user="100",scope=card.unified_msg_origin)
    assert manager.invite_for(same)["room_id"]=="test"
    assert manager.invite_for(event("陪我听歌",user="100",scope="qq-chatrobot-local:FriendMessage:100")) is None
    assert manager.invite_for(event("陪我听歌",user="other",scope=card.unified_msg_origin)) is None
    assert manager.invite_for(event("陪我听歌",user="100",scope="qq-chatrobot-local:GroupMessage:group-b")) is None
    req=NS(system_prompt="大肥鱼")
    manager.prepare(same,req)
    assert "本轮有真实网易云一起听邀请" in req.system_prompt
    now[0]+=601
    assert manager.invite_for(same) is None


@pytest.mark.asyncio
async def test_bridge_same_account_result_is_distinct_from_other_failures(monkeypatch):
    from app.services import netease_member

    monkeypatch.setattr(netease_member,"_bridge_connection",lambda settings:("http://127.0.0.1:3010","fixture-token"))
    monkeypatch.setattr(netease_member,"request_bytes",AsyncMock(return_value=json.dumps({
        "provider":"netease_listen","code":200,"success":False,"self_invite":True}).encode()))
    with pytest.raises(netease_member.NeteaseSelfInviteError):
        await netease_member.listen_together(NS(),"accept",room_id="real",inviter_id="456")


@pytest.mark.asyncio
async def test_room_heartbeat_failure_sends_terminal_text_instead_of_claiming_continued_listening(monkeypatch,tmp_path):
    manager=listening.MusicListening(soundtry.SoundtryJobs(),ListeningMemory(tmp_path/"music.db"))
    e=event("一起听歌")
    api=AsyncMock(side_effect=[{"in_room":True,"room_id":"real","song_id":"123"},{"success":False}])
    monkeypatch.setattr(listening,"listen_together",api)
    manager.room_owner=manager.jobs.owner(e)
    await manager._room(e,NS(),NS(),{"room_id":"real","song_id":"123"})
    assert api.await_count==2 and api.await_args.args[1]=="heartbeat"
    assert "暂时不能确认还在房间" in e.send.await_args.args[0]
    assert not manager.jobs.jobs and manager.room_owner is None


@pytest.mark.parametrize("text",["肥鱼","肥鱼，陪我聊聊","我今天很开心，肥鱼"])
def test_feiyu_name_wakes_at_start_or_end_despite_passive_cooldown(text):
    from test_astrbot_group_chat import Event

    from app.astrbot_group_chat import GroupChatRouter
    router=GroupChatRouter({"group_chat_enabled":True,"group_chat_groups":["1000000001"],
        "group_chat_bot_names":listening.ALIASES,"group_chat_help_requests":False,
        "group_chat_min_interval_seconds":600},clock=lambda:1000)
    e=Event(text)
    state=router._state(e)
    state.last_reply_at,state.passive_unanswered=999,True
    assert router.observe_event(e)=="name" and e.is_at_or_wake_command


def test_qq_voice_pack_changes_only_the_copy_not_other_platform_defaults(monkeypatch,tmp_path):
    from app.config import Settings
    from app.services import qq_voice
    monkeypatch.setattr(qq_voice,"ROOT",tmp_path)
    directory=tmp_path/"data/surfvoice"
    directory.mkdir(parents=True)
    profile=directory/"qq-voice.json"
    monkeypatch.setattr(qq_voice,"PROFILE_PATH",profile)
    files=[directory/name for name in ("ref.wav","gpt.ckpt","sovits.pth")]
    for path in files:path.write_bytes(b"fixture")
    profile.write_text(json.dumps({"enabled":True,"profile":{"label":"大肥鱼","ref_audio_path":str(files[0])},
        "gpt_weight":str(files[1]),"sovits_weights":{"zh":str(files[2])}}),encoding="utf-8")
    base=Settings(_env_file=None,voice_profile_default="murasame",
        voice_profiles_json='{"murasame":{"label":"小丛雨"}}',voice_gpt_weights_by_profile_json='{"murasame":"original"}')
    before=base.model_dump()
    qq=qq_voice.qq_voice_settings(base)
    assert qq.voice_profile_default=="dafeiyu" and base.model_dump()==before
    assert json.loads(qq.voice_gpt_weights_by_profile_json)["murasame"]=="original"
    assert json.loads(qq.voice_profiles_json)["murasame"]["label"]=="小丛雨"


@pytest.mark.parametrize("name",["大肥鱼","肥鱼","鲸鱼娘","小鲸鱼"])
def test_choosing_whale_voice_keeps_name_as_argument(name):
    from app.astrbot_group_chat import normalize_natural_command
    from app.services.voice_persona import resolve_voice_persona_alias
    assert normalize_natural_command("帮我选择音色 "+name,names=listening.ALIASES)=="选择音色 "+name
    assert resolve_voice_persona_alias(name,{"dafeiyu":{"label":"大肥鱼"}})=="dafeiyu"


@pytest.mark.asyncio
@pytest.mark.parametrize("point,region",[(-1,"0,0,1,1"),(float("nan"),"0,0,1,1"),(0,"0,0,2,1"),(0,"0,0,-1,1")])
async def test_video_detail_rejects_invalid_points_and_regions_without_reading_files(point,region):
    from app.astrbot_video_skill import read_detail
    with pytest.raises(ValueError):
        await read_detail(NS(),event(),NS(),point,region)


@pytest.mark.asyncio
async def test_local_video_audio_is_opt_in_and_reports_real_coverage(monkeypatch,tmp_path):
    from app import astrbot_video as video
    from app.services import video_audio
    monkeypatch.setattr(video_audio,"transcribe_video_audio",AsyncMock(return_value=None))
    folder=tmp_path/"models/medium"
    folder.mkdir(parents=True)
    (folder/"model.bin").write_bytes(b"fixture")
    monkeypatch.setattr(soundtry,"STATE",tmp_path)
    analyze=AsyncMock(return_value={"data":{"duration":6,"cues":[{"start":0,"text":"确实读到的音轨"}]}})
    monkeypatch.setattr(soundtry,"analyze_file",analyze)
    monkeypatch.setattr(video,"_run",AsyncMock())
    monkeypatch.setattr(video,"_binary",lambda *a:"ffmpeg")
    assert await video._audio_evidence(tmp_path/"v.mp4",NS(),{}) is None
    analyze.assert_not_awaited()
    result=await video._audio_evidence(tmp_path/"v.mp4",NS(),{"provider_settings":{"qqchat_local_soundtry":True}})
    assert "确实读到的音轨" in result.transcript and result.processed_seconds==6
    assert result.truncated and result.source=="soundtry_local"
