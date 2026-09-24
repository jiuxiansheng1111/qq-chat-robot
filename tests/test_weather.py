import httpx
import pytest

from app.config import Settings
from app.services import weather as weather_module


@pytest.mark.asyncio
async def test_fetch_weather_formats_open_meteo_payload(monkeypatch):
    async def handler(request: httpx.Request):
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "新加坡",
                            "country": "新加坡",
                            "latitude": 1.29,
                            "longitude": 103.85,
                            "timezone": "Asia/Singapore",
                        }
                    ]
                },
            )
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "timezone": "Asia/Singapore",
                    "current": {
                        "temperature_2m": 30.2,
                        "apparent_temperature": 35.1,
                        "relative_humidity_2m": 73,
                        "precipitation": 0.0,
                        "weather_code": 2,
                        "wind_speed_10m": 9.8,
                    },
                    "daily": {
                        "time": ["2026-09-24", "2026-09-25", "2026-09-26"],
                        "weather_code": [2, 80, 61],
                        "temperature_2m_max": [31.4, 31.0, 30.8],
                        "temperature_2m_min": [26.2, 26.0, 25.8],
                        "precipitation_probability_max": [35, 65, 55],
                    },
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs.pop("proxy", None)
        kwargs["trust_env"] = False
        kwargs["transport"] = transport
        return original_client(**kwargs)

    async def no_proxy(_settings):
        return {"trust_env": False}

    monkeypatch.setattr(weather_module, "outbound_httpx_kwargs", no_proxy)
    monkeypatch.setattr(weather_module.httpx, "AsyncClient", mocked_client)

    report = await weather_module.fetch_weather("新加坡", Settings(_env_file=None))
    text = weather_module.format_weather_report(report)
    assert report.location == "新加坡"
    assert "30.2°C" in text
    assert "体感 35.1°C" in text
    assert "明天" in text
    assert "Open-Meteo" in text


@pytest.mark.asyncio
async def test_fetch_weather_raises_for_unknown_place(monkeypatch):
    async def handler(request: httpx.Request):
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs.pop("proxy", None)
        kwargs["trust_env"] = False
        kwargs["transport"] = transport
        return original_client(**kwargs)

    async def no_proxy(_settings):
        return {"trust_env": False}

    monkeypatch.setattr(weather_module, "outbound_httpx_kwargs", no_proxy)
    monkeypatch.setattr(weather_module.httpx, "AsyncClient", mocked_client)

    with pytest.raises(weather_module.WeatherServiceError, match="没有找到地点"):
        await weather_module.fetch_weather("不存在的地方", Settings(_env_file=None))
