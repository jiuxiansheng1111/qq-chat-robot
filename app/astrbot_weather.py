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
    if not city:
        return {"status": "missing_location"}
    if len(city) > 80 or any(char in city for char in ("\n", ":", "/", "@")):
        return {"status": "invalid_location"}
    try:
        async with asyncio.timeout(30):
            report = await fetch_weather(city, plugin.runtime._core.settings)
    except (WeatherServiceError, TimeoutError) as exc:
        _LOGGER.warning(
            "QQ天气查询未完成 city=%s error=%s cause=%s", city, exc, type(exc.__cause__).__name__
        )
        return {"status": "unavailable", "message": "本轮没有取得实时天气，不能猜测天气数值"}
    return {
        "status": "ok",
        "source": report.source,
        "weather": asdict(report),
        "report": format_weather_report(report),
    }


async def handle_command(plugin, event):
    if not is_qq(event) or plugin.runtime is None or getattr(event, "is_stopped", lambda: False)():
        return False
    text, addressed = _addressed_text(event)
    if not addressed and not text.startswith("/"):
        return False
    city = parse_weather_location(text)
    if city is None:
        return False
    result = await query(plugin, event, city)
    if result["status"] == "denied":
        return False
    if result["status"] == "ok":
        reply = result["report"]
    elif result["status"] in {"missing_location", "invalid_location"}:
        reply = "想查哪个城市的天气？"
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
