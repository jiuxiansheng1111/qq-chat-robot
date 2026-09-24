import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.services import anime_character as anime
from app.services.image_resolution import ImageResolution


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


def test_short_names_and_near_names_require_safe_resolution():
    yoshino = anime.resolve_anime_character_matches("芳乃")
    assert len(yoshino) == 1
    assert yoshino[0].character.name == "朝武芳乃"
    assert yoshino[0].exact is True

    kirito = anime.resolve_anime_character_matches("桐谷和人")
    assert len(kirito) == 1
    assert kirito[0].character.name == "桐人"
    assert kirito[0].exact is True

    typo = anime.resolve_anime_character_matches("芳野")
    assert typo
    assert typo[0].character.name == "朝武芳乃"
    assert typo[0].exact is False


def test_murasame_has_disambiguated_search_terms():
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]
    assert "ムラサメ" in character.aliases
    assert "Murasame" in character.aliases
    queries = anime._search_queries(character, character.aliases)
    assert any("千恋" in query and "丛雨" in query for query in queries)
    assert any("緑髪" in query or "green hair" in query for query in queries)


def test_every_catalog_character_has_name_series_and_search_queries():
    assert len(anime.ANIME_CHARACTER_ROSTER) == 300
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
    monkeypatch.setattr(anime, "_bangumi_image", none)
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
    assert result.data == expected
    assert result.provider == "搜索引擎首图"


@pytest.mark.asyncio
async def test_legacy_provider_result_gets_traceable_source_page():
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]

    async def legacy_provider(*args, **kwargs):
        return jpeg_base64()

    result = await anime._anime_source_result(
        character,
        character.aliases,
        Settings(_env_file=None),
        "Bangumi",
        legacy_provider,
    )
    assert result.provider == "Bangumi"
    assert result.source_page_url.startswith("https://bgm.tv/")


@pytest.mark.asyncio
async def test_image_group_prefers_higher_resolution_candidate():
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]
    settings = Settings(_env_file=None)
    errors: list[str] = []

    async def low(*args, **kwargs):
        return jpeg_base64(320, 480)

    async def high(*args, **kwargs):
        return jpeg_base64(1200, 1600)

    winner = await anime._run_anime_source_group(
        character,
        character.aliases,
        settings,
        (("Bing图片", low), ("Bangumi", high)),
        2,
        errors,
    )
    assert winner is not None
    assert winner.provider == "Bangumi"
    assert winner.width == 1200
    assert winner.height == 1600


@pytest.mark.asyncio
async def test_profile_uses_moegirl_evidence_and_llm_rewrites_it(monkeypatch):
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]
    captured = {}

    async def fake_moegirl(*args, **kwargs):
        return (
            "丛雨是《千恋＊万花》的登场角色，寄宿于丛雨丸并担任神社神使。",
            "https://zh.moegirl.org.cn/丛雨",
        )

    async def fake_search(*args, **kwargs):
        return []

    class FakeLLM:
        async def ask(self, messages):
            captured["messages"] = messages
            return (
                "角色简介：丛雨是《千恋＊万花》的重要角色，寄宿于丛雨丸，"
                "并以神社神使的身份与主人公及其他角色相遇。她外表年幼，"
                "言行带有古风气质，互动中既有嘴硬的一面，也会表现出体贴与责任感。\n"
                "角色背景：她与建实神社、丛雨丸及作品的神秘传承紧密相连，"
                "故事会通过她与同伴的交流逐步揭开身份和过去。"
            )

    monkeypatch.setattr(anime, "_moegirl_character_profile", fake_moegirl)
    monkeypatch.setattr(anime, "search_web", fake_search)
    anime._ANIME_PROFILE_CACHE.clear()
    text = await anime.resolve_anime_character_profile(
        character,
        Settings(_env_file=None, moegirl_image_provider_enabled=True),
        FakeLLM(),
    )
    prompt_text = "\n".join(item["content"] for item in captured["messages"])
    assert "萌娘百科条目摘要" in prompt_text
    assert "角色简介：" in text and "角色背景：" in text
    assert "https://zh.moegirl.org.cn/丛雨" in text


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
    monkeypatch.setattr(anime, "_bangumi_image", none)
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
    assert first.data == expected
    assert calls == 1

    async def explode(*args, **kwargs):
        raise AssertionError("network should not run after cache hit")

    monkeypatch.setattr(anime, "_web_page_character_image", explode)
    second = await anime.resolve_anime_character_image(character, settings)
    assert second.data == expected
    assert second.cache_hit is True


@pytest.mark.asyncio
async def test_moegirl_api_returns_attributed_exact_character_image(monkeypatch):
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]
    raw = jpeg_data(480, 720)

    async def handler(request: httpx.Request):
        if request.url.host == "zh.moegirl.org.cn":
            assert request.url.params["action"] == "query"
            assert request.url.params["generator"] == "search"
            assert "千恋" in request.url.params["gsrsearch"]
            assert request.url.params["prop"] == "pageimages|info|extracts|categories"
            assert "imageinfo" not in request.url.params
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": [
                            {
                                "title": "丛雨",
                                "fullurl": "https://zh.moegirl.org.cn/丛雨",
                                "extract": "《千恋＊万花》中的角色。",
                                "categories": [{"title": "分类:千恋＊万花"}],
                                "original": {"source": "https://img.example/murasame.jpg"},
                            }
                        ]
                    }
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
    async def fake_download(client, url, referer, settings):
        assert url.endswith("murasame.jpg")
        assert referer.endswith("/丛雨")
        return jpeg_base64(480, 720)

    monkeypatch.setattr(anime, "_download_image", fake_download)
    result = await anime._moegirl_image(
        character,
        character.aliases,
        Settings(_env_file=None, moegirl_image_provider_enabled=True),
    )
    assert result is not None
    assert result.provider == "萌娘百科"
    assert result.source_page_url.endswith("/丛雨")
    assert result.image_url.endswith("murasame.jpg")
    assert "千恋" in result.evidence


@pytest.mark.asyncio
async def test_moegirl_rejects_wrong_character_even_when_work_matches(monkeypatch):
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]

    async def handler(request: httpx.Request):
        if request.url.host == "zh.moegirl.org.cn":
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": [
                            {
                                "title": "朝武芳乃",
                                "extract": "《千恋＊万花》中的角色。",
                                "original": {"source": "https://img.example/wrong.jpg"},
                            }
                        ]
                    }
                },
            )
        raise AssertionError("wrong identity image must not be downloaded")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(anime.httpx, "AsyncClient", mocked_client)
    result = await anime._moegirl_image(
        character,
        character.aliases,
        Settings(_env_file=None, moegirl_image_provider_enabled=True),
    )
    assert result is None


@pytest.mark.asyncio
async def test_anime_cache_sidecar_preserves_attribution(monkeypatch, tmp_path):
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]
    expected = jpeg_base64(720, 960)
    source = ImageResolution(
        data=expected,
        provider="测试来源",
        source_page_url="https://example.test/character",
        image_url="https://example.test/image.jpg",
        label="丛雨（千恋＊万花）",
        evidence="测试证据",
    )

    async def success(*args, **kwargs):
        return source

    async def none(*args, **kwargs):
        return None

    for name in (
        "_bangumi_image", "_anilist_image", "_web_page_character_image",
        "_wikipedia_image", "_moegirl_image", "_baidu_image", "_bing_image",
        "_bing_image_relaxed", "_search_engine_first_image",
    ):
        monkeypatch.setattr(anime, name, none)
    monkeypatch.setattr(anime, "_vndb_image", success)
    settings = Settings(_env_file=None, anime_image_cache_dir=str(tmp_path))
    first = await anime.resolve_anime_character_image(character, settings)
    metadata_path = anime._anime_image_cache_metadata_path(
        anime._anime_image_cache_path(character, settings)
    )
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["provider"] == "测试来源"
    second = await anime.resolve_anime_character_image(character, settings)
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.source_page_url == source.source_page_url


@pytest.mark.asyncio
async def test_anime_winner_cancels_late_source_before_cache_write(monkeypatch, tmp_path):
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]
    fast = ImageResolution(data=jpeg_base64(), provider="fast", label=character.name)
    late = ImageResolution(data=jpeg_base64(800, 900), provider="late", label=character.name)
    cancelled = False

    async def quick(*args, **kwargs):
        return fast

    async def slow(*args, **kwargs):
        nonlocal cancelled
        try:
            await __import__("asyncio").sleep(10)
        except __import__("asyncio").CancelledError:
            cancelled = True
            raise
        return late

    async def none(*args, **kwargs):
        return None

    for name in (
        "_bangumi_image", "_anilist_image", "_web_page_character_image",
        "_wikipedia_image", "_moegirl_image", "_baidu_image", "_bing_image",
        "_bing_image_relaxed", "_search_engine_first_image",
    ):
        monkeypatch.setattr(anime, name, none)
    monkeypatch.setattr(anime, "_vndb_image", quick)
    monkeypatch.setattr(anime, "_bangumi_image", slow)
    settings = Settings(_env_file=None, anime_image_cache_dir=str(tmp_path))
    result = await anime.resolve_anime_character_image(character, settings)
    cached = anime._load_anime_image_cache(character, settings)
    assert result.provider == "fast"
    assert cached is not None and cached.provider == "fast"
    assert cancelled is True


@pytest.mark.asyncio
async def test_resolver_hard_deadline_prevents_long_hang(monkeypatch, tmp_path):
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]

    async def slow(*args, **kwargs):
        await __import__("asyncio").sleep(1)

    for name in (
        "_vndb_image",
        "_bangumi_image",
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



@pytest.mark.asyncio
async def test_bangumi_resolves_chinese_character_art(monkeypatch):
    character = anime.ANIME_CHARACTER_BY_NAME["樱岛麻衣"]
    raw = jpeg_data(300, 420)

    async def handler(request: httpx.Request):
        if request.url.host == "api.bgm.tv" and request.url.path.endswith(
            "/v0/search/characters"
        ):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 123,
                            "name": "桜島麻衣",
                            "name_cn": "樱岛麻衣",
                            "images": {
                                "large": "https://img.example/mai.jpg",
                            },
                            "infobox": [],
                        }
                    ]
                },
            )
        if request.url.host == "api.bgm.tv" and request.url.path.endswith(
            "/v0/characters/123/subjects"
        ):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "青春ブタ野郎はバニーガール先輩の夢を見ない",
                        "name_cn": "青春猪头少年不会梦到兔女郎学姐",
                    }
                ],
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
    result = await anime._bangumi_image(
        character,
        character.aliases,
        Settings(_env_file=None),
    )
    assert result is not None
    decoded = base64.b64decode(result.removeprefix("base64://"))
    with Image.open(BytesIO(decoded)) as image:
        assert image.width == 300
        assert image.height == 420



def test_anime_catalog_renders_as_single_jpeg():
    rendered = anime.render_anime_character_catalog()
    assert rendered.startswith("base64://")
    raw = base64.b64decode(rendered.removeprefix("base64://"))
    with Image.open(BytesIO(raw)) as image:
        assert image.format == "JPEG"
        assert image.width >= 1600
        assert image.height >= 2000


@pytest.mark.asyncio
async def test_bangumi_uses_dedicated_character_image_endpoint_when_payload_has_no_image(
    monkeypatch,
):
    character = anime.ANIME_CHARACTER_BY_NAME["丛雨"]
    raw = jpeg_data(250, 300)

    async def handler(request: httpx.Request):
        if request.url.host == "api.bgm.tv" and request.url.path.endswith(
            "/v0/search/characters"
        ):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 456,
                            "name": "ムラサメ",
                            "images": None,
                            "infobox": [
                                {"key": "别名", "value": [{"k": "中文名", "v": "丛雨"}]}
                            ],
                        }
                    ]
                },
            )
        if request.url.host == "api.bgm.tv" and request.url.path.endswith(
            "/v0/characters/456/subjects"
        ):
            return httpx.Response(
                200,
                json=[{"name": "千恋＊万花", "name_cn": "千恋＊万花"}],
            )
        if request.url.host == "api.bgm.tv" and request.url.path.endswith(
            "/v0/characters/456/image"
        ):
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
    result = await anime._bangumi_image(
        character,
        character.aliases,
        Settings(_env_file=None),
    )
    assert result is not None
    decoded = base64.b64decode(result.removeprefix("base64://"))
    with Image.open(BytesIO(decoded)) as image:
        assert image.width == 250
        assert image.height == 300



@pytest.mark.asyncio
async def test_moegirl_pageimages_resolves_hange(monkeypatch):
    character = anime.AnimeCharacter(
        "韩吉·佐耶",
        "《进击的巨人》",
        "调查兵团成员。",
        ("ハンジ・ゾエ", "Hange Zoe"),
    )
    raw = jpeg_data(420, 640)

    async def handler(request: httpx.Request):
        if request.url.path.endswith("/api.php"):
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": {
                            "123": {
                                "pageid": 123,
                                "title": "韩吉·佐耶",
                                "original": {
                                    "source": "https://commons.example/hange.jpg"
                                },
                            }
                        }
                    }
                },
            )
        if request.url.host == "commons.example":
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
    result = await anime._moegirl_image(
        character,
        character.aliases,
        Settings(_env_file=None, moegirl_image_provider_enabled=True),
    )
    assert result is not None
    assert result.provider == "萌娘百科"
    decoded = base64.b64decode(result.data.removeprefix("base64://"))
    with Image.open(BytesIO(decoded)) as image:
        assert image.width == 420
        assert image.height == 640
