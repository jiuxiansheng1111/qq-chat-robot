import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image

import app.services.anime_character as anime
from app.config import Settings


def jpeg_data(width: int = 640, height: int = 800) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (220, 220, 220)).save(output, format="JPEG")
    return output.getvalue()


def test_anya_common_aliases_are_present():
    character = anime.ANIME_CHARACTER_BY_NAME["阿尼亚·福杰"]
    assert "阿尼亚·佛杰" in character.aliases
    assert "アーニャ・フォージャー" in character.aliases
    assert "Anya Forger" in character.aliases


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
    assert result.startswith("base64://")
    decoded = base64.b64decode(result.removeprefix("base64://"))
    with Image.open(BytesIO(decoded)) as image:
        assert image.format == "JPEG"


@pytest.mark.asyncio
async def test_resolver_uses_llm_only_to_expand_search_terms(monkeypatch):
    character = anime.ANIME_CHARACTER_BY_NAME["阿尼亚·福杰"]
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def no_wikipedia(character, aliases, settings):
        calls.append(("wikipedia", aliases))
        return None

    async def no_baidu(character, aliases, settings):
        calls.append(("baidu", aliases))
        return None

    async def bing(character, aliases, settings):
        calls.append(("bing", aliases))
        if any("Anya Forger SPY FAMILY" in value for value in aliases):
            return "base64://YW55YQ=="
        return None

    class FakeLLM:
        async def ask(self, messages):
            return "Anya Forger SPY FAMILY\nアーニャ・フォージャー SPY×FAMILY"

    monkeypatch.setattr(anime, "_wikipedia_image", no_wikipedia)
    monkeypatch.setattr(anime, "_baidu_image", no_baidu)
    monkeypatch.setattr(anime, "_bing_image", bing)

    result = await anime.resolve_anime_character_image(
        character,
        Settings(_env_file=None),
        FakeLLM(),
    )
    assert result == "base64://YW55YQ=="
    assert any("Anya Forger SPY FAMILY" in aliases for _, aliases in calls)
