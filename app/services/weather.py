from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass, replace

import httpx

from app.config import Settings
from app.services.http_routing import outbound_httpx_kwargs


class WeatherServiceError(RuntimeError):
    pass


def parse_weather_location(text: str) -> str | None:
    """Recognize a weather lookup, not quoted commands or weather discussion.

    The caller must establish that the message is addressed to this bot first.
    An empty string means a weather request with no explicit city.
    """
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) > 120 or re.search(
        r'["“”‘’「」『』`]|^>|不要|不用|别查|无需|为什么|为何|原理|命令|功能|失败|报错', value
    ):
        return None
    value = re.sub(
        r"^(?:(?:请|麻烦)(?:你)?[，, ]*)?(?:(?:帮我|给我|替我)(?:查(?:一下|下)?|看看|看一下|看下)?)?",
        "",
        value,
    )
    value = value.rstrip(" ？?！!。吧呀呢")
    if re.fullmatch(r"(?:你那(?:里|边|儿)|机器人(?:所在(?:地|城市)|本机)|本机|按(?:机器人|本机)(?:的)?IP)(?:的)?天气(?:怎么样|如何)?", value, re.IGNORECASE):
        return "本机"
    if (re.fullmatch(r"(?:我这(?:里|边|儿)|我(?:的)?所在地|我当地|我的城市)(?:的)?天气(?:怎么样|如何)?", value)
            or re.fullmatch(r"(?:按|根据)(?:我(?:的)?|我的公网)IP(?:定位)?(?:查|看)?(?:一下)?(?:当地|本地)?(?:的)?天气", value, re.IGNORECASE)):
        return "我这里"
    if value in {"本地天气", "当地天气", "这边天气", "今天冷不冷", "今天热不热"}:
        return ""
    if value in {
        "天气",
        "天气预报",
        "天气怎么样",
        "今天天气",
        "今天天气怎么样",
        "明天天气怎么样",
        "/天气",
        "/weather",
    }:
        return ""
    for prefix in ("/天气", "/weather", "查天气", "天气预报", "天气", "weather"):
        if value.casefold().startswith(prefix):
            suffix = value[len(prefix) :]
            if (
                suffix
                and not suffix.startswith((" ", "：", ":"))
                and prefix in {"天气", "天气预报", "weather"}
            ):
                continue
            rest = value[len(prefix) :].strip(" ：:")
            if not rest or re.fullmatch(r"[\w\u4e00-\u9fff·. -]{1,60}", rest):
                return rest
            return None
    match = re.fullmatch(
        r"(?:查(?:一下|下)?|看看|看一下|看下)?(?:今天|今日|明天|后天)?"
        r"(?P<city>[\w\u4e00-\u9fff·. -]{1,60}?)(?:的)?(?:今天|今日|明天|后天)?(?:的)?"
        r"天气(?:怎么样|如何|预报|情况)?",
        value,
        re.IGNORECASE,
    )
    if match:
        city = match["city"].strip()
        return "" if city in {"今天", "今日", "明天", "后天", "现在", "这里", "这边"} else city
    return None


async def _weather_json(url: str, params: dict, settings: Settings) -> dict:
    """Public idempotent weather GETs: direct first, existing route on I/O failure.

    This does not change the routing of chat, authorization or other services.
    HTTP errors and invalid payloads are not retried through another route.
    """
    options = {
        "timeout": max(4.0, min(float(settings.media_timeout_seconds), 12.0)),
        "follow_redirects": True,
        "headers": {
            "User-Agent": "qq-chat-robot/0.1 weather",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
    }
    try:
        async with httpx.AsyncClient(trust_env=False, **options) as client:
            response = await client.get(url, params=params)
    except httpx.RequestError:
        async with httpx.AsyncClient(**options, **await outbound_httpx_kwargs(settings)) as client:
            response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


WEATHER_CODE_TEXT = {
    0: "晴",
    1: "大致晴朗",
    2: "多云",
    3: "阴",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    56: "冻毛毛雨",
    57: "较强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "较强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}


@dataclass(frozen=True)
class WeatherDay:
    date: str
    condition: str
    high_c: float | None
    low_c: float | None
    precipitation_probability: float | None


@dataclass(frozen=True)
class WeatherReport:
    location: str
    timezone: str
    temperature_c: float | None
    apparent_temperature_c: float | None
    humidity_percent: float | None
    wind_kph: float | None
    precipitation_mm: float | None
    condition: str
    days: tuple[WeatherDay, ...]
    source: str = "Open-Meteo"


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _weather_text(code) -> str:
    try:
        return WEATHER_CODE_TEXT.get(int(code), f"天气代码 {int(code)}")
    except (TypeError, ValueError):
        return "天气状况未知"


def _display_location(item: dict) -> str:
    values = []
    for key in ("name", "admin1", "country"):
        value = str(item.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    return "，".join(values)


async def fetch_weather(location: str, settings: Settings) -> WeatherReport:
    """Bound the primary source, using the independent source only for I/O outages."""
    try:
        async with asyncio.timeout(12):
            return await _fetch_open_meteo(location, settings)
    except WeatherServiceError as exc:
        if not isinstance(exc.__cause__, httpx.RequestError):
            raise
    except TimeoutError:
        pass
    from app.services.weather_fallback import fetch_wttr_weather

    try:
        async with asyncio.timeout(15):
            return await fetch_wttr_weather(location, settings)
    except (ValueError, httpx.HTTPError, TimeoutError) as exc:
        raise WeatherServiceError("实时天气来源暂时无法访问") from exc


async def _fetch_open_meteo(location: str, settings: Settings) -> WeatherReport:
    location = str(location or "").strip()
    if not location:
        raise WeatherServiceError("缺少城市或地区")

    try:
        geo_payload = await _weather_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": location, "count": 5, "language": "zh", "format": "json"},
            settings,
        )
    except (ValueError, httpx.HTTPError) as exc:
        raise WeatherServiceError("天气地点解析失败") from exc

    results = geo_payload.get("results") if isinstance(geo_payload, dict) else None
    if not isinstance(results, list) or not results:
        raise WeatherServiceError(f"没有找到地点：{location}")
    place = next((item for item in results if isinstance(item, dict)), None)
    if not place:
        raise WeatherServiceError(f"没有找到地点：{location}")

    return await _forecast_for_place(place, settings)


_network_place = None


async def fetch_network_weather(settings: Settings) -> WeatherReport:
    """Use the bot's own direct network and city coordinates, never a QQ sender or relay address."""
    global _network_place
    if _network_place is None or _network_place[0] <= time.monotonic():
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                response = await client.get(
                    "https://ipwho.is/",
                    params={"fields": "success,city,region,country,latitude,longitude", "lang": "zh-CN"},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WeatherServiceError("未能估算机器人网络所在城市") from exc
        if not isinstance(data, dict) or data.get("success") is not True or not data.get("city"):
            raise WeatherServiceError("网络定位没有返回城市")
        lat, lon = _number(data.get("latitude")), _number(data.get("longitude"))
        if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
            raise WeatherServiceError("网络定位没有返回有效城市坐标")
        place = {"name": data["city"], "admin1": data.get("region"), "country": data.get("country"),
                 "latitude": round(lat, 2), "longitude": round(lon, 2)}
        _network_place = (time.monotonic() + 1800, place)
    place = _network_place[1]
    try:
        async with asyncio.timeout(12):
            return await _forecast_for_place(place, settings)
    except WeatherServiceError as exc:
        if not isinstance(exc.__cause__, httpx.RequestError):
            raise
    except TimeoutError:
        pass
    from app.services.weather_fallback import fetch_wttr_weather
    try:
        async with asyncio.timeout(15):
            # The coordinate pair keeps cities with the same name distinct.
            report = await fetch_wttr_weather(f"{place['latitude']},{place['longitude']}", settings)
            return replace(report, location=_display_location(place))
    except (ValueError, httpx.HTTPError, TimeoutError) as exc:
        raise WeatherServiceError("实时天气来源暂时无法访问") from exc


async def _forecast_for_place(place: dict, settings: Settings) -> WeatherReport:
    latitude = _number(place.get("latitude"))
    longitude = _number(place.get("longitude"))
    if latitude is None or longitude is None:
        raise WeatherServiceError("地点坐标无效")

    try:
        payload = await _weather_json(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": (
                    "temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "precipitation,weather_code,wind_speed_10m"
                ),
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max"
                ),
                "timezone": "auto",
                "forecast_days": 3,
            },
            settings,
        )
    except (ValueError, httpx.HTTPError) as exc:
        raise WeatherServiceError("实时天气接口请求失败") from exc

    if not isinstance(payload, dict):
        raise WeatherServiceError("实时天气返回格式异常")
    current = payload.get("current")
    daily = payload.get("daily")
    if not isinstance(current, dict) or not isinstance(daily, dict):
        raise WeatherServiceError("实时天气缺少必要数据")

    dates = daily.get("time") if isinstance(daily.get("time"), list) else []
    codes = daily.get("weather_code") if isinstance(daily.get("weather_code"), list) else []
    highs = (
        daily.get("temperature_2m_max") if isinstance(daily.get("temperature_2m_max"), list) else []
    )
    lows = (
        daily.get("temperature_2m_min") if isinstance(daily.get("temperature_2m_min"), list) else []
    )
    pops = (
        daily.get("precipitation_probability_max")
        if isinstance(daily.get("precipitation_probability_max"), list)
        else []
    )

    days = []
    for index, date in enumerate(dates[:3]):
        days.append(
            WeatherDay(
                date=str(date),
                condition=_weather_text(codes[index] if index < len(codes) else None),
                high_c=_number(highs[index]) if index < len(highs) else None,
                low_c=_number(lows[index]) if index < len(lows) else None,
                precipitation_probability=_number(pops[index]) if index < len(pops) else None,
            )
        )

    return WeatherReport(
        location=_display_location(place),
        timezone=str(payload.get("timezone") or place.get("timezone") or ""),
        temperature_c=_number(current.get("temperature_2m")),
        apparent_temperature_c=_number(current.get("apparent_temperature")),
        humidity_percent=_number(current.get("relative_humidity_2m")),
        wind_kph=_number(current.get("wind_speed_10m")),
        precipitation_mm=_number(current.get("precipitation")),
        condition=_weather_text(current.get("weather_code")),
        days=tuple(days),
    )


def _fmt(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "暂无"
    rounded = round(float(value), 1)
    if rounded.is_integer():
        return f"{int(rounded)}{suffix}"
    return f"{rounded}{suffix}"


def format_weather_report(report: WeatherReport) -> str:
    lines = [
        f"☀️ {report.location}天气",
        (
            f"现在：{report.condition}，{_fmt(report.temperature_c, '°C')}"
            f"（体感 {_fmt(report.apparent_temperature_c, '°C')}）"
        ),
        (
            f"湿度：{_fmt(report.humidity_percent, '%')}｜"
            f"风速：{_fmt(report.wind_kph, ' km/h')}｜"
            f"当前降水：{_fmt(report.precipitation_mm, ' mm')}"
        ),
    ]
    advice = thermal_advice(report)
    if advice:
        lines.insert(2, advice)
    if report.days:
        lines.append("")
        names = ("今天", "明天", "后天")
        for index, day in enumerate(report.days):
            lines.append(
                f"{names[index] if index < len(names) else day.date}：{day.condition}｜"
                f"{_fmt(day.low_c, '°C')}～{_fmt(day.high_c, '°C')}｜"
                f"最高降水概率 {_fmt(day.precipitation_probability, '%')}"
            )
    if report.timezone:
        lines.append(f"数据时区：{report.timezone}")
    lines.append(f"数据源：{report.source}")
    return "\n".join(lines)


def thermal_advice(report: WeatherReport) -> str:
    temperature = report.apparent_temperature_c
    if temperature is None:
        temperature = report.temperature_c
    if temperature is None:
        return ""
    if temperature <= 5:
        return "外面挺冷，出门穿厚一点，风大的地方记得把外套扣好"
    if temperature <= 15:
        return "今天有点凉，出门带件外套吧"
    if temperature >= 35:
        return "外面很热，出门带上水，能走阴凉处就走阴凉处"
    if temperature >= 28:
        return "今天有点热，出门穿透气些，记得喝水"
    return ""
