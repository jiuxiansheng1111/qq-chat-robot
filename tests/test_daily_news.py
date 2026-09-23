import pytest

import app.main as main
from app.services.web_search import SearchResult


@pytest.mark.asyncio
async def test_daily_news_digest_contains_five_unique_items(monkeypatch):
    async def fake_search(query: str, limit: int = 5, timeout: float = 12):
        prefix = "科技" if "科技" in query else "国际" if "国际" in query else "综合"
        return [
            SearchResult(
                title=f"{prefix}热点-{index}",
                url=f"https://news.example/{prefix}/{index}",
                snippet=f"{prefix}新闻摘要 {index}",
            )
            for index in range(1, 7)
        ]

    monkeypatch.setattr(main, "search_web", fake_search)
    digest = await main.build_daily_news_digest()

    assert "小丛雨 · 今日热点" in digest
    for index in range(1, 6):
        assert f"{index}." in digest
    assert digest.count("https://news.example/") == 5


def test_daily_news_defaults_to_noon_shanghai():
    assert main.settings.daily_news_enabled is True
    assert main.settings.daily_news_hour == 12
    assert main.settings.daily_news_minute == 0
    assert main.settings.daily_news_timezone == "Asia/Shanghai"
