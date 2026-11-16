import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import astrbot_group_radio as radio
from tests.activity_fixtures import ActivityEvent, host_at, turn


@pytest.mark.asyncio
async def test_radio_rotates_and_replays_a_real_record_without_duplicate_broadcast(tmp_path, monkeypatch):
    host, tool = host_at(tmp_path), radio.GroupRadio()
    alice = ("qq","99","100","11")
    bob = (*alice[:3],"12")
    song = SimpleNamespace(title="海风",artist="鲸鱼",page_url="https://music.163.com/#/song?id=22")
    monkeypatch.setattr(radio,"search_netease_music",AsyncMock(return_value=[song]))
    assert "第1期" in await turn(tool,host,alice,"1","开个群电台 海边")
    await turn(tool,host,bob,"2","加入电台")
    assert "海风" in await turn(tool,host,bob,"3","电台选歌 海风；送给大家")
    monkeypatch.setattr(radio,"attached_voice",AsyncMock(return_value=None))
    assert "两分钟" in await turn(tool,host,bob,"4","电台录串词")
    monkeypatch.setattr(radio,"attached_voice",AsyncMock(return_value=b"voice-from-bob"))
    assert "录音" in await turn(radio.GroupRadio(),host,bob,"5","")
    assert "主持人" in await turn(tool,host,bob,"6","发布电台")
    record = AsyncMock()
    monkeypatch.setattr(radio,"send_voice",record)
    ev1, ev2 = ActivityEvent(alice,"7"), ActivityEvent(alice,"7")
    await asyncio.gather(*(tool.handle(host,ev,alice,"发布这期电台") for ev in (ev1,ev2)))
    assert record.await_count == 1 and record.await_args.args[1] == b"voice-from-bob"
    texts = [call.args[0] for ev in (ev1,ev2) for call in ev.send.await_args_list]
    assert "送给大家" in texts[0] and "song?id=22" in texts[0] and "开场串词" in texts[1]
    assert "由12主持" in await turn(radio.GroupRadio(),host,alice,"8","开个群电台 星空")
    assert "已播出" in await turn(tool,host,bob,"9","电台第1期回放")
    await turn(tool,host,bob,"10","听电台第1期第1段串词")
    assert record.await_count == 2
    assert "还没有" in await turn(tool,host,("qq","99","101","12"),"11","电台第1期回放")
    assert "已退出" in await turn(tool,host,bob,"12","退出电台")
    assert "已取消" in await turn(tool,host,alice,"13","取消这期电台")
