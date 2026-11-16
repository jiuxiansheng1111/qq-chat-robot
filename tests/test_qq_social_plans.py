import asyncio
from datetime import datetime, timedelta
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.astrbot_followups import Followups
from app.astrbot_group_chat import GroupChatRouter
from app.astrbot_meetups import Meetups
from app.services.social_plans import CHINA, clock_for, day_for

IDS = count()
MONDAY = datetime(2026, 11, 16, 10, tzinfo=CHINA)


def event(user="10", group="20", bot="99", platform="qq-local", ident=None):
    raw = {"post_type": "message", "message_type": "group" if group else "private", "sender": {"nickname": "玩家" + user},
           "user_id": user, "self_id": bot, "message_id": ident or str(next(IDS)), "group_id": group}
    return SimpleNamespace(get_platform_id=lambda: platform, message_obj=SimpleNamespace(raw_message=raw),
                           unified_msg_origin=f"{platform}:{'GroupMessage' if group else 'FriendMessage'}:{group or user}")


def host():
    router = GroupChatRouter({"group_chat_groups": ["20", "30"]})
    api = AsyncMock()
    api.call_action.side_effect = lambda action, **params: {"user_id": params["self_id"]} if action == "get_login_info" else {"message_id": "1"}
    adapter = SimpleNamespace(meta=lambda: SimpleNamespace(name="aiocqhttp"), get_client=lambda: SimpleNamespace(api=api))
    context = SimpleNamespace(get_config=lambda _: {}, get_platform_inst=lambda _: adapter)
    return SimpleNamespace(context=context, _get_group_router=lambda: router, _legacy_event_allowed=AsyncMock(return_value=True)), api


async def test_meetup_seats_arrivals_waitlist_and_restarts(tmp_path):
    plans = Meetups(tmp_path, lambda: MONDAY)
    assert "还差 1 人" in await plans.handle(event(), "周六打雀魂，差一个")
    for elsewhere in [event(group="30"), event(bot="98"), event(platform="other"), event(group="")]:
        assert await plans.handle(elsewhere, "我八点到") is None
    assert await plans.handle(event(user="12"), "我八点到医院看病") is None
    assert "还差 1 人" in await plans.handle(event(), "还差几个人？")
    assert "20:00" in await plans.handle(event(user="11"), "算我一个，八点到")
    result = await plans.handle(event(user="12"), "我九点才行")
    assert "人齐了" in result and "候补：玩家12" in result and "最早 20:00" in result
    assert "最早 21:00" in await plans.handle(event(user="11"), "我不来了")
    plans = Meetups(tmp_path, lambda: MONDAY)
    late = event(user="12")
    assert "21:10" in await plans.handle(late, "我晚十分钟")
    assert await plans.handle(late, "我晚十分钟") == ""
    assert "21:10" in await plans.handle(event(), "最早几点能开？")
    assert "发起人" in await plans.handle(event(user="12"), "取消这局")
    result = await plans.handle(event(), "改到周日晚上八点半")
    assert "周日" in result and "20:30" in result and "还差 3 人" in result
    assert "取消了" in await plans.handle(event(), "取消这局")
    assert await plans.handle(event(), "我八点到") is None


async def test_concurrent_group_signup_and_incompatible_times(tmp_path):
    plans = Meetups(tmp_path, lambda: MONDAY)
    assert "还差 3 人" in await plans.handle(event(), "周六晚上八点一起打雀魂")
    await asyncio.gather(plans.handle(event("11"), "我九点才行"), plans.handle(event("12"), "我八点半得走"))
    result = await plans.handle(event("13"), "算我一个")
    assert "人齐了" in result and "时间对不上" in result
    assert all("玩家" + uid in result for uid in ("10", "11", "12", "13"))


async def test_morning_table_keeps_am_and_organizer_can_withdraw(tmp_path):
    plans = Meetups(tmp_path, lambda: MONDAY)
    await plans.handle(event(), "周六上午一起打雀魂")
    assert "08:00" in await plans.handle(event("11"), "我八点到")
    assert await plans.handle(event(), "取消") is None
    assert "玩家11接着组织" in await plans.handle(event(), "我不来了")
    assert "09:00" in await plans.handle(event("11"), "改到九点")
    assert "取消了" in await plans.handle(event("11"), "取消这局")


def test_natural_dates_and_chinese_times():
    assert day_for("周末", MONDAY).day == 21 and day_for("下周六", MONDAY).day == 28
    assert day_for("周六", MONDAY + timedelta(days=6)).day == 28
    assert clock_for("我晚上九点一刻到").isoformat() == "21:15:00"
    assert clock_for("我08:30到", evening=True).hour == 8
    with pytest.raises(ValueError):
        clock_for("明天25:75")


async def test_opt_in_original_group_restart_and_once_only(tmp_path):
    clock = [MONDAY]
    plugin, api = host()
    service = Followups(tmp_path, lambda: clock[0])
    user = event()
    assert "19:00" in await service.handle(plugin, user, "我明天下午面试")
    for other in (event("11"), event(group="30"), event(group=""), event(bot="98"), event(platform="other")):
        assert await service.handle(plugin, other, "好") is None
    clock[0] += timedelta(days=2)
    await service.deliver_due(plugin)
    api.call_action.assert_not_awaited()
    await service.close()
    service = Followups(tmp_path, lambda: clock[0])
    assert "要我" in await service.handle(plugin, event(), "我明天下午面试")
    assert "20:30" in await service.handle(plugin, event(), "好，晚上八点半")
    assert "21:00" in await service.handle(plugin, event(), "面试回访改到明晚九点")
    await service.close()
    service = Followups(tmp_path, lambda: clock[0])
    clock[0] += timedelta(days=1, hours=10, minutes=50)
    await service.deliver_due(plugin)
    api.call_action.assert_not_awaited()
    clock[0] += timedelta(minutes=10)
    await asyncio.gather(service.deliver_due(plugin), service.deliver_due(plugin))
    await service.deliver_due(plugin)
    deliveries = [call for call in api.call_action.await_args_list if call.args == ("send_group_msg",)]
    assert len(deliveries) == 1
    assert deliveries[0].kwargs["self_id"] == "99" and deliveries[0].kwargs["group_id"] == "20"
    assert deliveries[0].kwargs["message"][0] == {"type": "at", "data": {"qq": "10"}}


async def test_private_cancel_acl_offline_and_delivery_failure(tmp_path):
    clock = [MONDAY]
    plugin, api = host()
    service = Followups(tmp_path, lambda: clock[0])
    assert await service.handle(plugin, event(), "我朋友明天面试") is None
    assert await service.handle(plugin, event(), "我明天不面试了") is None
    assert "19:00" in await service.handle(plugin, event(group=""), "我明天下午考试")
    assert "不再问" in await service.handle(plugin, event(group=""), "不用了")
    for user in ("10", "11"):
        assert "20:00" in await service.handle(plugin, event(user, group=""), "明晚八点问我考试怎么样")
    assert "不再问" in await service.handle(plugin, event("11", group=""), "考试不用问我了")
    assert "20:00" in await service.handle(plugin, event(group="20"), "明晚八点问我比赛怎么样")
    await service.close()
    service = Followups(tmp_path, lambda: clock[0])
    clock[0] += timedelta(days=2)
    plugin._legacy_event_allowed.side_effect = lambda raw: not raw.get("group_id")
    api.call_action.side_effect = lambda *_a, **_kw: {"user_id": "unrelated"}
    await service.deliver_due(plugin)
    assert all(call.args == ("get_login_info",) for call in api.call_action.await_args_list)
    async def uncertain(action, **params):
        if action == "get_login_info":
            return {"user_id": params["self_id"]}
        raise TimeoutError
    api.call_action.reset_mock()
    api.call_action.side_effect = uncertain
    await service.deliver_due(plugin)
    await service.deliver_due(plugin)
    assert [call.args for call in api.call_action.await_args_list] == [("get_login_info",), ("send_private_msg",)]
    assert api.call_action.await_args.kwargs["user_id"] == "10"


async def test_confirmation_belongs_to_requester_and_uses_requested_time(tmp_path):
    plugin, api = host()
    service = Followups(tmp_path, lambda: MONDAY)
    for text in ("我明天下午三点面试，考官会问我什么？", "他说，明晚八点问我面试怎么样", "不要明晚八点问我面试"):
        assert await service.handle(plugin, event(), text) is None
    assert "要我" in await service.handle(plugin, event(), "我明天下午面试")
    assert await service.handle(plugin, event("11"), "好") is None
    assert await service.handle(plugin, event(), "好") is None
    assert "20:00" in await service.handle(plugin, event(), "我明天下午两点面试，晚上八点问我怎么样")
    assert "不再问" in await service.handle(plugin, event(), "面试不用问我了")
    await service.close()
    api.call_action.assert_not_awaited()
