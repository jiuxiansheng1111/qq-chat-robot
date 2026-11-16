"""QQ weather has a direct addressed entry and a model-callable data tool."""

from __future__ import annotations

import asyncio
import logging
from copy import copy
from dataclasses import asdict

from app.astrbot_qq_persona import is_qq
from app.astrbot_singing import _addressed_text
from app.services.weather import (
    WeatherServiceError,
    fetch_network_weather,
    fetch_weather,
    format_weather_report,
    parse_weather_location,
)

_LOGGER = logging.getLogger(__name__)


def prepare(event, request):
    if is_qq(event):
        return
    original = getattr(request, "func_tool", None)
    if (
        original is not None
        and callable(getattr(original, "get_tool", None))
        and original.get_tool("qqchat_weather")
    ):
        scoped = copy(original)
        scoped.tools = list(original.tools)
        scoped.remove_tool("qqchat_weather")
        request.func_tool = scoped


async def query(plugin, event, location):
    if not is_qq(event) or plugin.runtime is None:
        return {"status": "unavailable"}
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if not await plugin._legacy_event_allowed(raw):
        return {"status": "denied"}
    city = str(location or "").strip()
    network = city in {"本机", "机器人", "你那里", "你那边"}
    personal = city in {"我这里", "我这边", "我的城市"}
    if not city or personal:
        from app.astrbot_weather_alerts import service_for
        from app.services.social_plans import scope
        service = service_for(plugin)
        key = scope(event) if service and callable(getattr(event, "get_platform_id", None)) else None
        city = await service.city_for(key) if key else ""
        if personal and not city:
            if key:
                from app.astrbot_weather_alerts import Change
                service.ask_city(key, "city", Change.ALL)
            return {"status": "missing_location", "message":
                    "想看你那边的天气，告诉我城市名就行。也可以用手机打开 https://wttr.in/?format=%25l 看自己网络估算的城市，再把城市名发我"}
        network = not city
    if len(city) > 80 or any(char in city for char in ("\n", ":", "/", "@")):
        return {"status": "invalid_location"}
    from app.astrbot_turn_features import remember_lookup

    remember_lookup(plugin, event, "weather", "本机" if network else city, retry=deliver)
    try:
        async with asyncio.timeout(30):
            settings = plugin.runtime._core.settings
            report = await fetch_network_weather(settings) if network else await fetch_weather(city, settings)
    except (WeatherServiceError, TimeoutError) as exc:
        _LOGGER.warning(
            "QQ天气查询未完成 city=%s error=%s cause=%s", city, exc, type(exc.__cause__).__name__
        )
        if network:
            return {"status": "location_unavailable", "message": "这次没查到机器人网络所在城市的天气，告诉我想查的城市名就好"}
        return {"status": "unavailable", "message": "本轮没有取得实时天气，不能猜测天气数值"}
    return {
        "status": "ok",
        "source": report.source,
        "weather": asdict(report),
        "location_basis": "机器人网络出口估算" if network else "指定城市",
        "report": ("按机器人网络出口估算的位置查到的天气：\n" if network else "") + format_weather_report(report),
    }


async def handle_command(plugin, event):
    if not is_qq(event) or plugin.runtime is None or getattr(event, "is_stopped", lambda: False)():
        return False
    text, addressed = _addressed_text(event)
    if await handle_alert_command(plugin, event, text, addressed):
        return True
    if not addressed and not text.startswith("/"):
        return False
    city = parse_weather_location(text)
    if city is None:
        return False
    return await deliver(plugin, event, city)


async def handle_alert_command(plugin, event, text=None, addressed=False):
    from app.astrbot_weather_alerts import parse_request, service_for, weather_enabled
    from app.services.social_plans import scope

    if not is_qq(event) or not callable(getattr(event, "get_platform_id", None)):
        return False
    if text is None:
        text, addressed = _addressed_text(event)
    service = service_for(plugin)
    if not text or service is None:
        return False
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if not isinstance(raw, dict) or not raw.get("user_id") or not raw.get("self_id"):
        return False
    key = scope(event)
    request = parse_request(text)
    if not request and (not addressed or key not in service.pending):
        return False
    router = plugin._get_group_router()
    if router.ignored_sender(event) or (key[2] and not router.allows_companion_turn(event, addressed)):
        return False
    if not await plugin._legacy_event_allowed(raw) or not await weather_enabled(plugin, key):
        return False
    reply = await service.handle(plugin, event, text)
    if reply is None:
        return False
    event.should_call_llm(False)
    event.stop_event()
    if reply:
        await event.send(event.plain_result(reply))
    return True


async def deliver(plugin, event, city):
    result = await query(plugin, event, city)
    if result["status"] == "denied":
        return False
    if result["status"] == "ok":
        reply = result["report"]
    elif result["status"] in {"missing_location", "invalid_location", "location_unavailable"}:
        reply = result.get("message") or "想查哪个城市的天气？"
    else:
        from app.astrbot_failure_reply import Failure, describe_failure

        reply = await describe_failure(
            plugin.context, event, Failure("这次天气查询", "unavailable")
        )
    event.should_call_llm(False)
    try:
        await event.send(event.plain_result(reply))
    finally:
        event.stop_event()
    return True
