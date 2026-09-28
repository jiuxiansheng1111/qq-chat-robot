import httpx
import pytest

from app.services.anime_character import AnimeCharacter
from scripts.audit_moegirl_catalog_sources import (
    fetch_pages,
    match_page,
    non_match_classification,
)


@pytest.mark.asyncio
async def test_fetch_pages_retries_transient_status_before_succeeding():
    request = httpx.Request("GET", "https://zh.moegirl.org.cn/api.php")

    class Client:
        def __init__(self):
            self.calls = 0

        async def get(self, url, *, params):
            self.calls += 1
            if self.calls == 1:
                return httpx.Response(503, request=request)
            return httpx.Response(200, request=request, json={"query": {"pages": []}})

    client = Client()
    assert await fetch_pages(
        client,
        titles=["丛雨"],
        retries=1,
        retry_backoff=0,
    ) == []
    assert client.calls == 2


def test_match_page_keeps_series_evidence_failure_separate_from_missing_image():
    character = AnimeCharacter("丛雨", "《千恋＊万花》", "角色", ("ムラサメ",))
    source, diagnostics = match_page(character, [{
        "title": "丛雨",
        "extract": "来自另一部作品的角色。",
        "original": {"source": "https://img.moegirl.org.cn/murasame.jpg"},
    }])

    assert source is None
    assert diagnostics["identity_title_matches"] == 1
    assert diagnostics["series_evidence_misses"] == 1
    assert non_match_classification(diagnostics) == "series_evidence_missing"
