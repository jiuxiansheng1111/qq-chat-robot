import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from activity_fixtures import host_at, turn

from app import astrbot_time_capsule as capsule
from app.services.social_plans import CHINA


async def test_group_time_lock_projects_separate_authors_and_opens_once(tmp_path,monkeypatch):
    now = [datetime(2026,11,17,15,tzinfo=CHINA)]
    host,key = host_at(tmp_path),("qq","bot","group","A")
    other = ("qq","bot","group","B")
    tool = capsule.TimeCapsule(lambda:now[0])
    monkeypatch.setattr(capsule.TimeCapsule,"start",lambda self,host:None)
    api = SimpleNamespace(call_action=AsyncMock(return_value={"message_id":10}))
    monkeypatch.setattr(capsule,"scheduled_allowed",AsyncMock(return_value=True))
    monkeypatch.setattr(capsule,"original_qq_client",AsyncMock(return_value=api))
    monkeypatch.setattr(capsule,"attached_photo",AsyncMock(return_value=b"fixture-photo"))
    monkeypatch.setattr(capsule,"attached_voice",AsyncMock(return_value=b"fixture-voice"))
    assert "20:00" in await turn(tool,host,key,1,"建个群时间胶囊 2026-11-18 20:00")
    assert "封好" in await turn(tool,host,key,2,"时间胶囊里放：明年见")
    assert not await turn(tool,host,key,2,"时间胶囊里放：明年见")
    assert "封好" in await turn(tool,host,other,3,"时间胶囊留言 私人草稿")
    assert "1条" in await turn(tool,host,other,4,"撤回我的胶囊投稿")
    report = await turn(tool,host,key,5,"打开群时间胶囊")
    assert "1条" in report and "明年见" not in report
    await tool.check_due(host)
    api.call_action.assert_not_awaited()
    assert "没有" in await turn(tool,host,("qq","bot","other","A"),6,"打开时间胶囊")
    now[0]+=timedelta(days=2)
    restarted = capsule.TimeCapsule(lambda:now[0])
    await asyncio.gather(tool.check_due(host),restarted.check_due(host))
    assert api.call_action.await_count == 3
    calls = str(api.call_action.await_args_list)
    assert "明年见" in calls and "私人草稿" not in calls and "'record'" in calls and "'image'" in calls
    await restarted.check_due(host)
    assert api.call_action.await_count == 3
