import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.services import anime_character as anime


def jpeg_data(width: int = 640, height: int = 800) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (220, 220, 220)).save(
        output,
        format="JPEG",
    )
    return output.getvalue()


def jpeg_base64(width: int = 640, height: int = 800) -> str:
    return "base64://" + base64.b64encode(jpeg_data(width, height)).decode()


def test_anya_common_aliases_are_present():
    character = anime.ANIME_CHARACTER_BY_NAME["阿尼亚·福杰"]
    assert "阿尼亚·佛杰" in character.aliases
    assert "アーニャ・フォージャー" in character.aliases
    assert "Anya Forger" in character.aliases


def test_murasame_has_disambiguated_search_terms():
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]
    assert "ムラサメ" in character.aliases
    assert "Murasame" in character.aliases
    queries = anime._search_queries(character, character.aliases)
    assert any("千恋" in query and "丛雨" in query for query in queries)
    assert any("緑髪" in query or "green hair" in query for query in queries)


def test_every_catalog_character_has_name_series_and_search_queries():
    assert len(anime.ANIME_CHARACTER_ROSTER) >= 40
    for character in anime.ANIME_CHARACTER_ROSTER:
        assert character.name.strip()
        assert character.series.strip()
        queries = anime._search_queries(character, character.aliases)
        assert queries
        assert any(character.name in query for query in queries)


def test_candidate_score_accepts_alias_plus_series():
    character = anime.ANIME_CHARACTER_BY_NAME["阿尼亚·福杰"]
    score = anime._candidate_score(
        character,
        (),
        "Anya Forger - SPY×FAMILY / 间谍过家家 character visual",
    )
    assert score >= 9


def test_candidate_score_rejects_series_only():
    character = anime.ANIME_CHARACTER_BY_NAME["阿尼亚·福杰"]
    score = anime._candidate_score(
        character,
        (),
        "SPY×FAMILY 间谍过家家 official visual",
    )
    assert score == 0


@pytest.mark.asyncio
async def test_bing_image_accepts_english_alias(monkeypatch):
    character = anime.ANIME_CHARACTER_BY_NAME["阿尼亚·福杰"]
    metadata = (
        '{"murl":"https://img.example/anya.jpg",'
        '"purl":"https://example.com/anya",'
        '"t":"Anya Forger - SPY x FAMILY"}'
    )
    html = '<a class="iusc" m="' + metadata.replace('"', '&quot;') + '"></a>'
    raw = jpeg_data()

    async def handler(request: httpx.Request):
        if request.url.host == "www.bing.com":
            return httpx.Response(200, text=html)
        if request.url.host == "img.example":
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=raw,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(anime.httpx, "AsyncClient", mocked_client)

    result = await anime._bing_image(
        character,
        character.aliases,
        Settings(_env_file=None),
    )
    assert result is not None
    decoded = base64.b64decode(result.removeprefix("base64://"))
    with Image.open(BytesIO(decoded)) as image:
        assert image.format == "JPEG"


@pytest.mark.asyncio
async def test_resolver_races_sources_and_uses_first_image_fallback(
    monkeypatch,
    tmp_path,
):
    character = anime.ANIME_CHARACTER_BY_NAME["阿尼亚·福杰"]
    expected = jpeg_base64()

    async def none(*args, **kwargs):
        return None

    async def first(*args, **kwargs):
        return expected

    monkeypatch.setattr(anime, "_vndb_image", none)
    monkeypatch.setattr(anime, "_anilist_image", none)
    monkeypatch.setattr(anime, "_web_page_character_image", none)
    monkeypatch.setattr(anime, "_wikipedia_image", none)
    monkeypatch.setattr(anime, "_baidu_image", none)
    monkeypatch.setattr(anime, "_bing_image", none)
    monkeypatch.setattr(anime, "_bing_image_relaxed", none)
    monkeypatch.setattr(anime, "_search_engine_first_image", first)

    settings = Settings(
        _env_file=None,
        anime_image_cache_dir=str(tmp_path / "anime-cache"),
        anime_image_resolve_timeout_seconds=2,
    )
    result = await anime.resolve_anime_character_image(
        character,
        settings,
        object(),
    )
    assert result == expected


@pytest.mark.asyncio
async def test_resolver_persists_cache_and_skips_network_next_time(
    monkeypatch,
    tmp_path,
):
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]
    expected = jpeg_base64(720, 960)
    calls = 0

    async def strict_success(*args, **kwargs):
        nonlocal calls
        calls += 1
        return expected

    async def none(*args, **kwargs):
        return None

    monkeypatch.setattr(anime, "_vndb_image", none)
    monkeypatch.setattr(anime, "_anilist_image", none)
    monkeypatch.setattr(anime, "_web_page_character_image", strict_success)
    monkeypatch.setattr(anime, "_wikipedia_image", none)
    monkeypatch.setattr(anime, "_baidu_image", none)
    monkeypatch.setattr(anime, "_bing_image", none)
    monkeypatch.setattr(anime, "_bing_image_relaxed", none)
    monkeypatch.setattr(anime, "_search_engine_first_image", none)

    settings = Settings(
        _env_file=None,
        anime_image_cache_dir=str(tmp_path / "anime-cache"),
        anime_image_resolve_timeout_seconds=1,
    )
    first = await anime.resolve_anime_character_image(character, settings)
    assert first == expected
    assert calls == 1

    async def explode(*args, **kwargs):
        raise AssertionError("network should not run after cache hit")

    monkeypatch.setattr(anime, "_web_page_character_image", explode)
    second = await anime.resolve_anime_character_image(character, settings)
    assert second == expected


@pytest.mark.asyncio
async def test_resolver_hard_deadline_prevents_long_hang(monkeypatch, tmp_path):
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]

    async def slow(*args, **kwargs):
        await __import__("asyncio").sleep(1)

    for name in (
        "_vndb_image",
        "_anilist_image",
        "_web_page_character_image",
        "_wikipedia_image",
        "_baidu_image",
        "_bing_image",
        "_bing_image_relaxed",
        "_search_engine_first_image",
    ):
        monkeypatch.setattr(anime, name, slow)

    settings = Settings(
        _env_file=None,
        anime_image_cache_dir=str(tmp_path / "anime-cache"),
        anime_image_resolve_timeout_seconds=0.2,
    )
    with pytest.raises(RuntimeError, match="没有找到"):
        await anime.resolve_anime_character_image(character, settings)



@pytest.mark.asyncio
async def test_vndb_resolves_murasame_character_art(monkeypatch):
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]
    raw = jpeg_data(256, 300)

    async def handler(request: httpx.Request):
        if request.url.host == "api.vndb.org":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "c-test",
                            "name": "Murasame",
                            "original": "ムラサメ",
                            "aliases": ["丛雨"],
                            "image": {
                                "url": "https://img.example/murasame.jpg",
                                "dims": [256, 300],
                            },
                            "vns": [
                                {
                                    "title": "Senren * Banka",
                                    "alttitle": "千恋＊万花",
                                }
                            ],
                        }
                    ]
                },
            )
        if request.url.host == "img.example":
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=raw,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(anime.httpx, "AsyncClient", mocked_client)
    result = await anime._vndb_image(
        character,
        character.aliases,
        Settings(_env_file=None),
    )
    assert result is not None
    decoded = base64.b64decode(result.removeprefix("base64://"))
    with Image.open(BytesIO(decoded)) as image:
        assert image.width == 256
        assert image.height == 300
