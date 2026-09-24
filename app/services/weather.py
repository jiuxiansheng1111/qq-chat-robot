from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import Settings
from app.services.http_routing import outbound_httpx_kwargs


class WeatherServiceError(RuntimeError):
    pass


WEATHER_CODE_TEXT = {
    0: "晴", 1: "大致晴朗", 2: "多云", 3: "阴", 45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "较强毛毛雨", 56: "冻毛毛雨",
    57: "较强冻毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨",
    67: "较强冻雨", 71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨", 85: "小阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴强冰雹",
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


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
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
    location = str(location or "").strip()
    if not location:
        raise WeatherServiceError("缺少城市或地区")

    timeout = max(4.0, min(float(settings.media_timeout_seconds), 12.0))
    client_kwargs = await outbound_httpx_kwargs(settings)
    headers = {
        "User-Agent": "qq-chat-robot/0.1 weather",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    }
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
        **client_kwargs,
    ) as client:
        try:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 5, "language": "zh", "format": "json"},
            )
            geo.raise_for_status()
            geo_payload = geo.json()
        except (ValueError, httpx.HTTPError) as exc:
            raise WeatherServiceError("天气地点解析失败") from exc

        results = geo_payload.get("results") if isinstance(geo_payload, dict) else None
        if not isinstance(results, list) or not results:
            raise WeatherServiceError(f"没有找到地点：{location}")
        place = next((item for item in results if isinstance(item, dict)), None)
        if not place:
            raise WeatherServiceError(f"没有找到地点：{location}")

        latitude = _number(place.get("latitude"))
        longitude = _number(place.get("longitude"))
        if latitude is None or longitude is None:
            raise WeatherServiceError("地点坐标无效")

        try:
            forecast = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
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
            )
            forecast.raise_for_status()
            payload = forecast.json()
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
    highs = daily.get("temperature_2m_max") if isinstance(daily.get("temperature_2m_max"), list) else []
    lows = daily.get("temperature_2m_min") if isinstance(daily.get("temperature_2m_min"), list) else []
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
        location=_display_location(place) or location,
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
    rounded = round(value, 1)
    if rounded.is_integer():
        return f"{int(rounded)}{suffix}"
    return f"{rounded}{suffix}"


def format_weather_report(report: WeatherReport) -> str:
    lines = [
        f"☀️ {report.location}天气",
        f"现在：{report.condition}，{_fmt(report.temperature_c, '°C')}"
        f"（体感 {_fmt(report.apparent_temperature_c, '°C')}）",
        f"湿度：{_fmt(report.humidity_percent, '%')}｜"
        f"风速：{_fmt(report.wind_kph, ' km/h')}｜"
        f"当前降水：{_fmt(report.precipitation_mm, ' mm')}",
    ]
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
    lines.append("数据源：Open-Meteo 实时天气")
    return "\n".join(lines)
