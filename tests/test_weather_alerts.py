import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from test_qq_social_plans import event, host

from app import astrbot_weather
from app import astrbot_weather_alerts as alerts
from app.services import weather
from app.services.social_plans import CHINA

EVENING = datetime(2026, 11, 17, 18, tzinfo=CHINA)


def report():
    return weather.WeatherReport("深圳", "Asia/Shanghai", 17, 12, 70, 11, 0, "多云", (
        weather.WeatherDay("2026-11-17", "阴", 24, 15, 0),
        weather.WeatherDay("2026-11-18", "小雨", 18, 9, 65),
    ))


@pytest.mark.parametrize("text,expected", [
    ("如果深圳下雨或变天时提醒我", ("subscribe", "深圳", alerts.Change.ALL)),
    ("帮我开启深圳的天气提醒", ("subscribe", "深圳", alerts.Change.ALL)),
    ("如果下雨的话记得提醒我带伞", ("subscribe", "", alerts.Change.RAIN)),
    ("北京升温时就提醒我", ("subscribe", "北京", alerts.Change.WARMER)),
    ("关闭天气提醒", ("cancel", "", alerts.Change.ALL)),
    ("他说深圳下雨提醒我", None), ("不要深圳降温提醒我", None),
    ("深圳下雨会冷吗", None), ("深圳天气", None), ("深圳下雨提醒小王", None),
])
def test_weather_requests(text, expected):
    assert alerts.parse_request(text) == expected


def test_consecutive_forecast_days_and_real_conditions():
    sample = report()
    date, message = alerts.next_day_notice(sample, EVENING, alerts.Change.ALL)
    assert date == "2026-11-18" and all(s in message for s in ("小雨", "24°C降到18°C", "15°C降到9°C"))
    assert alerts.next_day_notice(sample, EVENING + timedelta(days=1), alerts.Change.ALL) is None
    warmer = replace(sample.days[1], condition="多云", high_c=30, low_c=None, precipitation_probability=None)
    sample = replace(sample, days=(sample.days[0], warmer))
    assert "24°C升到30°C" in alerts.next_day_notice(sample, EVENING, alerts.Change.WARMER)[1]
    assert alerts.next_day_notice(sample, EVENING, alerts.Change.COLDER | alerts.Change.RAIN) is None
    for condition, pop, alert in (("多云", 60, True), ("多云", 59, False), ("小雪", 90, False), ("小雨", 20, True)):
        day = replace(warmer, condition=condition, precipitation_probability=pop)
        assert bool(alerts.next_day_notice(replace(sample, days=(sample.days[0], day)), EVENING, alerts.Change.RAIN)) == alert


@pytest.mark.parametrize("actual,feels,word", [
    (28, 5, "穿厚"), (22, 14, "外套"), (24, 36, "很热"), (21, 29, "有点热"),
    (9, None, "外套"), (19, 19, ""), (None, None, ""),
])
def test_thermal_advice_prefers_feels_like(actual, feels, word):
    sample = replace(report(), temperature_c=actual, apparent_temperature_c=feels)
    result = weather.thermal_advice(sample)
    assert (word in result and result in weather.format_weather_report(sample)) if word else not result


def setup_host(root):
    plugin, api = host()
    database = SimpleNamespace(is_blocked=AsyncMock(return_value=False), plugin_enabled=AsyncMock(return_value=True))
    plugin.runtime = SimpleNamespace(project_root=root, _core=SimpleNamespace(
        settings=None, app=SimpleNamespace(state=SimpleNamespace(db=database))))
    return plugin, api


def chat(text, **scope):
    e = event(**scope)
    e.get_message_str = lambda: text
    e.get_group_id = lambda: e.message_obj.raw_message.get("group_id", "")
    e.get_platform_name = lambda: "aiocqhttp"
    e.message_obj.message = []
    e.is_at_or_wake_command = False
    e.send, e.stop_event, e.should_call_llm = AsyncMock(), Mock(), Mock()
    e.plain_result = lambda text: text
    return e


async def test_query_selection_and_city_dialogue(tmp_path, monkeypatch):
    plugin, _ = setup_host(tmp_path)
    fetch = AsyncMock(return_value=report())
    network = AsyncMock(return_value=replace(report(), location="机器人网络城市"))
    monkeypatch.setattr(astrbot_weather, "fetch_weather", fetch)
    monkeypatch.setattr(astrbot_weather, "fetch_network_weather", network)
    monkeypatch.setattr(alerts, "fetch_weather", fetch)
    for text, expected in (("天气", "机器人网络"), ("我这里的天气", "城市名"), ("深圳", "记住了"),
                           ("今天天气", "外套"), ("你那边的天气", "机器人网络")):
        e = chat(text, group="")
        assert await astrbot_weather.handle_command(plugin, e)
        assert expected in e.send.await_args.args[0]
    assert network.await_count == 2
    assert "missing_location" == (await astrbot_weather.query(plugin, chat("天气", user="11", group=""), "我这里"))["status"]


async def test_opt_in_routes_persistence_dedup_and_cancellation(tmp_path, monkeypatch):
    plugin, api = setup_host(tmp_path)
    clock = [EVENING - timedelta(hours=1)]
    service = plugin._weather_alerts = alerts.WeatherAlerts(tmp_path, lambda: clock[0])
    fetch = AsyncMock(return_value=report())
    monkeypatch.setattr(alerts, "fetch_weather", fetch)
    try:
        for group in ("20", ""):
            e = chat("大肥鱼，深圳下雨或变天时提醒我", group=group)
            assert await astrbot_weather.handle_command(plugin, e)
        await service.check(plugin)
        api.call_action.assert_not_awaited()
    finally:
        await service.close()
    service = plugin._weather_alerts = alerts.WeatherAlerts(tmp_path, lambda: clock[0])
    clock[0] = EVENING
    fetch.reset_mock()
    plugin._legacy_event_allowed.return_value = False
    await service.check(plugin)
    fetch.assert_not_awaited()
    plugin._legacy_event_allowed.return_value = True
    plugin.runtime._core.app.state.db.plugin_enabled.return_value = False
    await service.check(plugin)
    fetch.assert_not_awaited()
    plugin.runtime._core.app.state.db.plugin_enabled.return_value = True
    api.call_action.side_effect = lambda action, **kw: {"user_id": "wrong-account"}
    await service.check(plugin)
    assert fetch.await_count == 1  # both subscriptions share the city forecast
    assert all(call.args == ("get_login_info",) for call in api.call_action.await_args_list)
    api.call_action.reset_mock()
    api.call_action.side_effect = lambda action, **kw: {"user_id": kw["self_id"]} if action == "get_login_info" else {"message_id": 1}
    await asyncio.gather(service.check(plugin), service.check(plugin))
    await service.check(plugin)
    sent = [c for c in api.call_action.await_args_list if c.args[0].startswith("send_")]
    assert sorted(c.args[0] for c in sent) == ["send_group_msg", "send_private_msg"]
    group = next(c for c in sent if c.args == ("send_group_msg",))
    assert group.kwargs["message"][0]["data"]["qq"] == "10" and group.kwargs["group_id"] == "20"
    for group in ("20", ""):
        e = chat("大肥鱼，取消天气提醒", group=group)
        assert await astrbot_weather.handle_command(plugin, e)
        assert "关闭" in e.send.await_args.args[0]
        assert not await service.city_for(alerts.scope(e))


async def test_cannot_deliver_after_cancel_during_forecast(tmp_path, monkeypatch):
    plugin, api = setup_host(tmp_path)
    service = alerts.WeatherAlerts(tmp_path, lambda: EVENING)
    monkeypatch.setattr(service, "start", lambda _: None)
    fetch = AsyncMock(return_value=report())
    monkeypatch.setattr(alerts, "fetch_weather", fetch)
    e = chat("天气", group="")
    await service.handle(plugin, e, "开启深圳天气提醒")
    async def cancel(*_):
        await service.handle(plugin, chat("天气", group=""), "取消天气提醒")
        return report()
    fetch.side_effect = cancel
    await service.check(plugin)
    assert all(c.args == ("get_login_info",) for c in api.call_action.await_args_list)


async def test_network_estimate_uses_direct_city_coordinates_and_cache(monkeypatch):
    calls = []
    def response(req):
        calls.append(req)
        assert req.url.host == "ipwho.is" and "ip," not in req.url.params["fields"]
        return httpx.Response(200, json={"success": True, "city": "同名城市", "region": "省",
            "country": "国家", "latitude": 22.541, "longitude": 114.054})
    original = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(response), **kwargs)
    monkeypatch.setattr(weather, "_network_place", None)
    monkeypatch.setattr(weather.httpx, "AsyncClient", client)
    forecast = AsyncMock(return_value=report())
    monkeypatch.setattr(weather, "_forecast_for_place", forecast)
    await weather.fetch_network_weather(None)
    await weather.fetch_network_weather(None)
    assert len(calls) == 1 and forecast.await_count == 2
    assert forecast.await_args.args[0]["latitude"] == 22.54 and forecast.await_args.args[0]["longitude"] == 114.05
