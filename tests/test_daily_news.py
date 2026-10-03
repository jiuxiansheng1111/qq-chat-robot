from datetime import datetime, timedelta
from zoneinfo import ZoneInfoNotFoundError

import pytest

from app import main
from app.config import Settings
from app.db.database import Database
from app.services.web_search import SearchResult


@pytest.mark.asyncio
async def test_daily_news_digest_contains_ten_unique_items(monkeypatch):
    async def fake_search(
        topic: str,
        *,
        limit: int = 20,
        timeout: float = 12,
        max_age_hours: int = 48,
    ):
        return [
            SearchResult(
                title=f"{topic}热点-{index}",
                url=f"https://news.example/{topic}/{index}",
                snippet=f"{topic}新闻摘要 {index}",
            )
            for index in range(1, 7)
        ]

    monkeypatch.setattr(main, "search_news_feed", fake_search)
    digest = await main.build_daily_news_digest()

    assert "小丛雨 · 今日热点" in digest
    assert "10 条最新热点" in digest
    for index in range(1, 11):
        assert f"{index}." in digest
    assert digest.count("https://news.example/") == 10


@pytest.mark.asyncio
async def test_daily_news_reuses_today_and_excludes_previous_days(tmp_path, monkeypatch):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "news.db")))
    await db.init()
    now = datetime.now(main._daily_news_timezone())
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    old_items = [
        SearchResult(
            title=f"昨天已经发送的热点 {index}",
            url=f"https://news.example/old/{index}?utm_source=qq",
            snippet="旧新闻",
        )
        for index in range(2)
    ]
    await db.save_daily_news_items(
        yesterday,
        [
            (
                main._daily_news_canonical_url(item.url),
                main._daily_news_title_key(item.title),
                item.title,
                item.url,
                item.snippet,
            )
            for item in old_items
        ],
    )

    calls = 0

    async def fake_search(
        topic: str,
        *,
        limit: int = 20,
        timeout: float = 12,
        max_age_hours: int = 48,
    ):
        nonlocal calls
        calls += 1
        return old_items + [
            SearchResult(
                title=f"今天全新热点 {index}",
                url=f"https://news.example/new/{index}",
                snippet=f"今天新闻摘要 {index}",
            )
            for index in range(15)
        ]

    monkeypatch.setattr(main, "search_news_feed", fake_search)
    first = await main.build_daily_news_digest(db)
    first_call_count = calls
    second = await main.build_daily_news_digest(db)

    assert "昨天已经发送的热点" not in first
    assert first.count("https://news.example/new/") == 10
    assert second == first
    assert calls == first_call_count


def test_daily_news_canonical_url_drops_tracking_parameters():
    left = main._daily_news_canonical_url(
        "https://www.news.example/story/1?utm_source=qq&id=7&from=share"
    )
    right = main._daily_news_canonical_url("http://news.example/story/1?id=7")

    assert left == right == "news.example/story/1?id=7"


def test_daily_news_defaults_to_noon_shanghai():
    assert main.settings.daily_news_enabled is True
    assert main.settings.daily_news_hour == 12
    assert main.settings.daily_news_minute == 0
    assert main.settings.daily_news_timezone == "Asia/Shanghai"


def test_daily_news_timezone_uses_utc8_without_tzdata(monkeypatch):
    calls: list[str] = []

    def unavailable(name: str):
        calls.append(name)
        raise ZoneInfoNotFoundError(name)

    monkeypatch.setattr(main, "ZoneInfo", unavailable)
    tz = main._daily_news_timezone()

    assert calls == [main.settings.daily_news_timezone]
    assert tz.utcoffset(None) == timedelta(hours=8)
    assert tz.tzname(None) == "Asia/Shanghai"
