from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app import astrbot_weather
from app.config import Settings
from app.services.weather import WeatherDay, WeatherReport, parse_weather_location


@pytest.mark.parametrize(
    "text,city",
    [
        ("深圳今天天气怎么样", "深圳"),
        ("深圳的天气", "深圳"),
        ("查一下深圳天气", "深圳"),
        ("帮我看看深圳明天天气", "深圳"),
        ("天气 深圳", "深圳"),
        ("/天气 深圳", "深圳"),
        ("weather Singapore", "Singapore"),
        ("天气怎么样", ""),
        ("今天天气怎么样", ""),
    ],
)
def test_weather_requests(text, city):
    assert parse_weather_location(text) == city


@pytest.mark.parametrize(
    "text",
    [
        "深圳天气不错",
        "天气不错",
        "不要查深圳天气",
        "为什么深圳天气这么热",
        "天气功能怎么用",
        "他说‘深圳天气’",
        "把深圳天气翻译成英文",
    ],
)
def test_discussion_is_not_a_lookup(text):
    assert parse_weather_location(text) is None


def event(text, *, group="100", at=None, platform="aiocqhttp"):
    return NS(
        get_message_str=lambda: text,
        get_group_id=lambda: group,
        get_platform_name=lambda: platform,
        is_at_or_wake_command=False,
        message_obj=NS(
            message=[],
            raw_message={
                "message_type": "group" if group else "private",
                "self_id": "300",
                "user_id": "200",
                "group_id": group,
                "message": [{"type": "at", "data": {"qq": at}}] if at else [],
            },
        ),
        should_call_llm=AsyncMock(),
        stop_event=AsyncMock(),
        send=AsyncMock(),
        plain_result=lambda text: text,
    )


def plugin():
    return NS(
        runtime=NS(_core=NS(settings=Settings(_env_file=None))),
        context=NS(),
        _legacy_event_allowed=AsyncMock(return_value=True),
    )


def report():
    return WeatherReport(
        "深圳，广东省，中国",
        "Asia/Shanghai",
        25,
        26,
        70,
        8,
        0,
        "多云",
        (WeatherDay("2026-11-03", "多云", 28, 22, 15),),
    )


@pytest.mark.parametrize(
    "group,text,at",
    [
        ("100", "小肥鱼深圳今天天气怎么样", None),
        ("100", "深圳今天天气怎么样", "300"),
        ("", "深圳的天气", None),
    ],
)
async def test_lookup_returns_real_report_without_needing_chat_model(monkeypatch, group, text, at):
    from unittest.mock import Mock

    fetch = AsyncMock(return_value=report())
    monkeypatch.setattr(astrbot_weather, "fetch_weather", fetch)
    monkeypatch.setattr(
        "app.astrbot_failure_reply.describe_failure",
        AsyncMock(side_effect=AssertionError("No model needed")),
    )
    e = event(text, group=group, at=at)
    e.should_call_llm = Mock()
    e.stop_event = Mock()
    assert await astrbot_weather.handle_command(plugin(), e)
    assert fetch.await_args.args[0] == "深圳"
    assert "25°C" in e.send.await_args.args[0] and "Open-Meteo" in e.send.await_args.args[0]
    e.should_call_llm.assert_called_once_with(False)
    e.stop_event.assert_called_once()


@pytest.mark.parametrize(
    "group,text,at,platform",
    [
        ("100", "深圳今天天气怎么样", None, "aiocqhttp"),
        ("100", "小肥鱼深圳今天天气怎么样", "someone-else", "aiocqhttp"),
        ("100", "小肥鱼深圳今天天气怎么样", None, "bilibili"),
    ],
)
async def test_unaddressed_other_recipient_and_other_platform_stay_untouched(
    monkeypatch, group, text, at, platform
):
    fetch = AsyncMock()
    monkeypatch.setattr(astrbot_weather, "fetch_weather", fetch)
    e = event(text, group=group, at=at, platform=platform)
    assert not await astrbot_weather.handle_command(plugin(), e)
    fetch.assert_not_awaited()
    e.send.assert_not_awaited()


async def test_weather_tool_preserves_acl_and_does_not_send(monkeypatch):
    fetch = AsyncMock(return_value=report())
    monkeypatch.setattr(astrbot_weather, "fetch_weather", fetch)
    p, e = plugin(), event("深圳天气")
    p._legacy_event_allowed.return_value = False
    assert (await astrbot_weather.query(p, e, "深圳"))["status"] == "denied"
    fetch.assert_not_awaited()
    p._legacy_event_allowed.return_value = True
    result = await astrbot_weather.query(p, e, "深圳")
    assert result["weather"]["temperature_c"] == 25 and result["status"] == "ok"
    e.send.assert_not_awaited()
