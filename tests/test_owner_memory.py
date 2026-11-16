import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from app.astrbot_owner_memory import music_portrait, prepare, recall, sent
from app.services import owner_memory as memory
from app.services.listening_memory import ListeningMemory


def event(text="久喜欢听这首歌",user="12345",scope="qq:FriendMessage:12345",mid="1",platform="aiocqhttp"):
    extras={}
    group="22222" if "GroupMessage" in scope else ""
    raw={"message_type":"group" if group else "private","user_id":user,"self_id":"99999",
         "message_id":mid,"group_id":group,"post_type":"message","sender":{"nickname":"久"},
         "message":[{"type":"text","data":{"text":text}}]}
    e=NS(unified_msg_origin=scope,message_obj=NS(raw_message=raw),get_platform_name=lambda:platform,
        get_message_str=lambda:text,get_sender_id=lambda:user,get_self_id=lambda:"99999",get_group_id=lambda:group,
        get_extra=lambda k,d=None:extras.get(k,d),set_extra=lambda k,v:extras.update({k:v}),
        _has_send_oper=False,result=None)
    e.get_result=lambda:e.result
    return e


@pytest.mark.parametrize("platform",["aiocqhttp","onebot","onebot_v11"])
def test_verified_owner_not_display_name(owner_enabled,platform):
    assert memory.event_owner(event(platform=platform))
    assert memory.event_owner(event(user="67890",platform=platform)) is None
    assert memory.event_owner(event(platform="bilibili")) is None


def test_private_and_each_group_are_separate_even_for_owner(owner_enabled):
    private=event("只在私聊说的草莓蛋糕")
    g1=event("本群说的内存卡",scope="qq:GroupMessage:22222")
    g2=event("另一个群",scope="qq:GroupMessage:33333")
    p=memory.record_incoming(private)
    memory.record_incoming(g1)
    memory.record_incoming(g2)
    assert len(recall(private,"草莓蛋糕")["matches"])==1
    assert recall(g1,"草莓蛋糕")["matches"]==[]
    assert recall(g2,entry_id=p)["status"]=="not_found"
    assert memory.record_incoming(event(user="67890"))==0


def test_archive_preserves_long_text_and_dedupe_without_a_twenty_item_cap(owner_enabled):
    long="开头"+"甲"*9500+"内存卡的真实结论"
    e=event(long)
    row=memory.record_incoming(e)
    assert memory.record_incoming(e)==row
    for i in range(2,45):
        memory.record_incoming(event(f"第{i}条",mid=str(i)))
    result=recall(e,"真实结论")["matches"][0]
    assert result["id"]==row and result["has_more"]
    assert result["total_characters"]==len(long)
    owner=memory.event_owner(e)
    assert memory.archive().read(owner,row,0,12000)["text"]==long
    with sqlite3.connect(memory.ARCHIVE_PATH) as db:
        assert db.execute("SELECT count(*) FROM owner_dialogue").fetchone()[0]==44


def test_quote_and_attachments_do_not_become_owner_words_or_raw_media(owner_enabled):
    e=event("我问的是内存卡")
    e.message_obj.raw_message["message"] += [
        {"type":"reply","data":{"id":"42","text":"其他人说电脑内存条"}},
        {"type":"image","data":{"file":"base64,VERY_PRIVATE_MEDIA"}}]
    memory.record_incoming(e)
    row=recall(e)["matches"][0]
    assert row["text"]=="我问的是内存卡"
    assert row["attachments"][0]["quoted_message_id"]=="42"
    assert "VERY_PRIVATE_MEDIA" not in memory.ARCHIVE_PATH.read_bytes().decode("utf-8",errors="ignore")


def test_credentials_are_not_preserved_as_chat_memory(owner_enabled):
    e=event("password=fixture-secret api_key:fixture-api 密码：fixture-password 喜欢晴天")
    memory.record_incoming(e)
    result=recall(e)["matches"][0]["text"]
    assert "fixture-" not in result and "喜欢晴天" in result


def test_literal_pinned_sources_and_music_preferences_are_distinct(owner_enabled):
    e=event("以后记住，我喜欢米津玄师的春雷，因为节奏轻快")
    assert memory.remember_statement(e,"我喜欢米津玄师的春雷，因为节奏轻快","music_preference")["status"]=="saved"
    assert memory.remember_statement(e,"我喜欢米津玄师","agreement")["status"]=="saved"
    assert memory.remember_statement(e,"他最喜欢所有摇滚乐","music_preference")["status"]=="not_saved"
    assert memory.remember_statement(event("天气不错",mid="2"),"天气不错")["status"]=="not_explicit"
    owner=memory.event_owner(e)
    assert len(memory.archive().notes(owner))==2
    source_id=memory.archive().notes(owner)[0]["entry_id"]
    assert memory.archive().forget(owner,source_id)==1
    assert memory.archive().notes(owner)==[]


@pytest.mark.parametrize("text,all_current,allowed",[
    ("不要忘记我",True,False),("别人说“忘记我”",True,False),
    ("忘记我上次说过的设备型号",True,False),("鲸鱼娘，忘记我吧",True,True),
    ("删除这条记忆",False,True),("清空本会话所有长期记忆",True,True),
])
def test_forget_requires_explicit_non_negated_request(text,all_current,allowed):
    assert memory.forget_request_allowed(text,all_current)==allowed


async def test_only_successfully_delivered_reply_is_archived(owner_enabled):
    e=event()
    Plain=type("Plain",(),{})
    part=Plain();part.text="今天听见这段旋律很开心"
    e.result=NS(chain=[part])
    await sent(e)
    assert not memory.ARCHIVE_PATH.exists()
    e._has_send_oper=True
    await sent(e)
    await sent(e)
    rows=recall(e)["matches"]
    assert len(rows)==1 and rows[0]["role"]=="assistant"
    assert not memory.record_assistant(e,"[QQCHAT_NO_REPLY]")


async def test_owner_identity_and_data_are_only_injected_once(owner_enabled):
    e=event()
    memory.remember_statement(event("记住，我喜欢晴天"),"我喜欢晴天")
    request=NS(system_prompt="原人格")
    await prepare(e,request)
    assert "久" in request.system_prompt and "qqchat_recall" in request.system_prompt
    assert '"owner_name": "久"' in request.system_prompt
    assert "12345" not in request.system_prompt
    initial=request.system_prompt
    await prepare(e,request)
    assert request.system_prompt==initial
    other=NS(system_prompt="其他群友不读画像")
    await prepare(event(user="67890"),other)
    assert other.system_prompt=="其他群友不读画像"


def test_music_portrait_separates_hearing_from_likes_and_preserves_events(owner_enabled,tmp_path):
    e=event("这首春雷听起来轻快，但不是我最喜欢的")
    memory.remember_statement(e,"这首春雷听起来轻快，但不是我最喜欢的","music_preference")
    listens=ListeningMemory(tmp_path/"music.sqlite3")
    owner=(e.unified_msg_origin,"12345")
    track={"song_id":"123","title":"春雷","artist":"米津玄師","aliases":["米津玄师"]}
    long="听感"+"节奏"*2500
    listens.remember(owner,track,{"report":"实际听觉证据"},long)
    listens.remember(owner,track,{"report":"第二次实际听觉证据"},"这次注意到鼓点")
    portrait=music_portrait(e,listens)
    assert portrait["heard_tracks"][0]["listen_count"]==2
    assert "不是我最喜欢" in portrait["owner_stated_preferences"][0]["text"]
    assert len(portrait["listening_events"])==2
    first=listens.timeline(owner)[1]
    assert first["reflection"]==long
    summary=listens.summaries(owner,budget=1800)
    assert len(json.dumps(summary,ensure_ascii=False))<1800
    assert listens.read_event(("other", "12345"),first["id"])["status"]=="not_found"
    assert music_portrait(event(user="67890"),listens)["status"]=="denied"
    listens.forget(owner,"123")
    assert listens.timeline(owner)==[]


async def test_manual_owner_notes_do_not_drop_after_twenty_and_style_learning_stays_off(tmp_path):
    from app.db.database import Database
    db=Database(NS(database_path=str(tmp_path/"legacy.sqlite3")))
    await db.init()
    for i in range(25):
        await db.add_long_term_memory("group","owner",f"约定{i}",max_items=None)
    assert len(await db.long_term_memories("group","owner",limit=None))==25
    await db.record_group_activity("group","other","群友","哈哈", "2026-11-03",learn_style=False)
    assert await db.fetchone("SELECT count(*) FROM group_style_stats")== (0,)
    assert await db.fetchone("SELECT message_count FROM daily_activity")== (1,)


def test_owner_explicit_command_keeps_full_text_bypasses_affection_and_denies_other_writes(owner_enabled,tmp_path,monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from app import main
    monkeypatch.setattr(main.settings,"database_path",str(tmp_path/"commands.sqlite3"))
    monkeypatch.setattr(main.settings,"onebot_api_base","http://127.0.0.1:9")
    monkeypatch.setattr(main.settings,"onebot_self_id","99999")
    monkeypatch.setattr(main,"send_group_message",AsyncMock())
    headers={"X-OneBot-Token":main.settings.onebot_webhook_token} if main.settings.onebot_webhook_token else {}
    text="我喜欢的具体安排"+"原文"*400
    with TestClient(main.app) as client:
        for user in ("12345","67890"):
            result=client.post("/onebot/webhook",headers=headers,json={
                "post_type":"message","message_type":"group","self_id":"99999","group_id":"22222",
                "user_id":user,"message_id":"command-"+user,"message":"/长期记忆 "+text,
                "sender":{"user_id":user,"role":"member"}})
            assert result.status_code==200
        own=client.portal.call(main.app.state.db.long_term_memories,"22222","12345")
        other=client.portal.call(main.app.state.db.long_term_memories,"22222","67890")
        assert own==[text] and other==[]
