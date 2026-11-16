from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from activity_fixtures import host_at, turn

from app import astrbot_study as study
from app.services.social_plans import CHINA


async def test_study_pause_resume_quiet_and_restart_single_delivery(tmp_path,monkeypatch):
    now = [datetime(2026,11,17,15,tzinfo=CHINA)]
    clock = lambda:now[0]
    host,key = host_at(tmp_path),("qq","bot","group","member")
    tool = study.Study(clock)
    monkeypatch.setattr(study.Study,"start",lambda self,host:None)
    approval = AsyncMock(return_value=True)
    api = SimpleNamespace(call_action=AsyncMock(return_value={"message_id":7}))
    monkeypatch.setattr(study,"scheduled_allowed",approval)
    monkeypatch.setattr(study,"original_qq_client",AsyncMock(return_value=api))
    assert "25分钟" in await turn(tool,host,key,1,"陪我自习25分钟，复习英语")
    origin = "qq:GroupMessage:group"
    assert await tool.quiet(host,origin) and not await tool.quiet(host,"qq:GroupMessage:else")
    now[0] += timedelta(minutes=3)
    assert "22分钟" in await turn(tool,host,key,2,"暂停自习")
    assert not await tool.quiet(host,origin)
    now[0] += timedelta(hours=3)
    await tool.check_due(host)
    api.call_action.assert_not_awaited()
    assert "22分钟" in await turn(tool,host,key,3,"继续自习")
    now[0] += timedelta(minutes=23)
    restarted = study.Study(clock)
    import asyncio
    await asyncio.gather(restarted.check_due(host),tool.check_due(host))
    api.call_action.assert_awaited_once()
    assert "英语" in str(api.call_action.await_args)
    assert "到了" in await turn(tool,host,key,4,"查看自习状态")
    await turn(tool,host,key,5,"取消自习")
