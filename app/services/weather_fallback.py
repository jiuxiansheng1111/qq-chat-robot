"""Independent public weather source for transient Open-Meteo connection failures.

The documented j1 API: https://github.com/chubin/wttr.in#json-output
Location names are always explicit; never use the caller's egress IP as a city.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from urllib.parse import quote

from app.services.weather import WeatherDay, WeatherReport, WeatherServiceError, _weather_json

_CONDITIONS = {
    "sunny": "晴",
    "clear": "晴",
    "partly cloudy": "局部多云",
    "cloudy": "多云",
    "overcast": "阴",
    "mist": "薄雾",
    "fog": "雾",
    "patchy rain nearby": "附近有零星降雨",
    "patchy rain possible": "可能有零星降雨",
    "light rain": "小雨",
    "moderate rain": "中雨",
    "heavy rain": "大雨",
    "light drizzle": "小毛毛雨",
    "light rain shower": "小阵雨",
    "thundery outbreaks nearby": "附近有雷暴",
    "light snow": "小雪",
}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _condition(item):
    for key in ("lang_zh", "weatherDesc"):
        values = item.get(key)
        if isinstance(values, list) and values and isinstance(values[0], dict):
            text = str(values[0].get("value") or "").strip()
            if text:
                return _CONDITIONS.get(text.casefold(), text)
    return "天气状况未知"


async def fetch_wttr_weather(location, settings):
    city = str(location or "").strip()
    if not city:
        raise WeatherServiceError("缺少城市或地区")
    payload = await _weather_json(
        "https://wttr.in/" + quote(city, safe=""), {"format": "j1", "lang": "zh"}, settings
    )
    if not isinstance(payload, dict):
        raise WeatherServiceError("备用天气返回格式异常")
    observations, forecasts = payload.get("current_condition"), payload.get("weather")
    if (
        not isinstance(observations, list)
        or not observations
        or not isinstance(observations[0], dict)
    ):
        raise WeatherServiceError("备用天气缺少观测数据")
    if not isinstance(forecasts, list) or not forecasts or not isinstance(forecasts[0], dict):
        raise WeatherServiceError("备用天气缺少日期")
    try:
        first_date = date.fromisoformat(str(forecasts[0].get("date", "")))
    except ValueError as exc:
        raise WeatherServiceError("备用天气日期无效") from exc
    # The location can be a day ahead/behind UTC; old cached forecasts are not current weather.
    if abs((first_date - datetime.now(UTC).date()).days) > 1:
        raise WeatherServiceError("备用天气数据已过期")
    current = observations[0]
    temperature = _number(current.get("temp_C"))
    if temperature is None:
        raise WeatherServiceError("备用天气缺少温度")
    days = []
    for day in forecasts[:3]:
        if not isinstance(day, dict):
            continue
        raw_hours = day.get("hourly")
        hours = (
            [hour for hour in raw_hours if isinstance(hour, dict)]
            if isinstance(raw_hours, list)
            else []
        )
        noon = next((hour for hour in hours if str(hour.get("time")) == "1200"), {})
        chances = [
            number
            for hour in hours
            for key in ("chanceofrain", "chanceofsnow")
            if (number := _number(hour.get(key))) is not None
        ]
        days.append(
            WeatherDay(
                date=str(day.get("date", "")),
                condition=_condition(noon) + "（午间）" if noon else "天气状况未知",
                high_c=_number(day.get("maxtempC")),
                low_c=_number(day.get("mintempC")),
                precipitation_probability=max(chances) if chances else None,
            )
        )
    return WeatherReport(
        # WWO's nearest station can be across a city boundary. Keep the actual requested
        # area's name, explicitly label nearby observations, and do not claim that station's
        # administrative country is the requested city's country.
        location=f"{city}附近",
        timezone="",
        temperature_c=temperature,
        apparent_temperature_c=_number(current.get("FeelsLikeC")),
        humidity_percent=_number(current.get("humidity")),
        wind_kph=_number(current.get("windspeedKmph")),
        precipitation_mm=_number(current.get("precipMM")),
        condition=_condition(current),
        days=tuple(days),
        source="wttr.in / WorldWeatherOnline（附近观测与预报）",
    )
