from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.services import weather, weather_fallback


def payload():
    return {
        "current_condition": [
            {
                "temp_C": "26",
                "FeelsLikeC": "27",
                "humidity": "77",
                "windspeedKmph": "18",
                "precipMM": "0.0",
                "weatherDesc": [{"value": "Cloudy "}],
            }
        ],
        "weather": [
            {
                "date": datetime.now(UTC).date().isoformat(),
                "mintempC": "24",
                "maxtempC": "29",
                "hourly": [
                    {
                        "time": "1200",
                        "chanceofrain": "20",
                        "chanceofsnow": "0",
                        "weatherDesc": [{"value": "Patchy rain nearby"}],
                    }
                ],
            }
        ],
    }


async def test_connection_failure_uses_independent_source_with_real_attribution(monkeypatch):
    broken_primary = AsyncMock(side_effect=httpx.ConnectError("synthetic connection outage"))
    fallback = AsyncMock(return_value=payload())
    monkeypatch.setattr(weather, "_weather_json", broken_primary)
    monkeypatch.setattr(weather_fallback, "_weather_json", fallback)
    result = await weather.fetch_weather("深圳", Settings(_env_file=None))
    assert result.location == "深圳附近" and result.temperature_c == 26
    assert result.condition == "多云" and result.days[0].precipitation_probability == 20
    assert result.days[0].high_c == 29 and "午间" in result.days[0].condition
    text = weather.format_weather_report(result)
    assert "wttr.in" in text and "Open-Meteo" not in text
    assert fallback.await_args.args[0] == "https://wttr.in/%E6%B7%B1%E5%9C%B3"
    assert fallback.await_args.args[1] == {"format": "j1", "lang": "zh"}


async def test_primary_deadline_can_use_backup(monkeypatch):
    monkeypatch.setattr(weather, "_fetch_open_meteo", AsyncMock(side_effect=TimeoutError))
    fallback = AsyncMock(return_value=payload())
    monkeypatch.setattr(weather_fallback, "_weather_json", fallback)
    assert (await weather.fetch_weather("深圳", Settings(_env_file=None))).temperature_c == 26
    fallback.assert_awaited_once()


async def test_old_cached_fallback_is_not_presented_as_current_weather(monkeypatch):
    data = payload()
    data["weather"][0]["date"] = (datetime.now(UTC).date() - timedelta(days=3)).isoformat()
    monkeypatch.setattr(weather_fallback, "_weather_json", AsyncMock(return_value=data))
    with pytest.raises(weather.WeatherServiceError, match="已过期"):
        await weather_fallback.fetch_wttr_weather("深圳", Settings(_env_file=None))


@pytest.mark.parametrize("temperature", [None, True, "NaN"])
async def test_missing_or_invalid_current_measurement_is_not_success(monkeypatch, temperature):
    data = payload()
    data["current_condition"][0]["temp_C"] = temperature
    monkeypatch.setattr(weather_fallback, "_weather_json", AsyncMock(return_value=data))
    with pytest.raises(weather.WeatherServiceError, match="缺少温度"):
        await weather_fallback.fetch_wttr_weather("深圳", Settings(_env_file=None))
