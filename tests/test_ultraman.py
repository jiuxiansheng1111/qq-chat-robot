import base64
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

import app.services.ultraman as ultraman_module
from app.services.ultraman import (
    ULTRAMAN_DEBUT_YEARS,
    ULTRAMAN_PROFILES,
    ULTRAMAN_ROSTER,
    official_ultraman_image,
    render_ultraman_card,
    render_ultraman_catalog,
    resolve_ultraman_query,
    ultraman_catalog_text_pages,
    ultraman_profile_text,
)


def test_every_ultraman_has_complete_collection_card_content():
    assert len(ULTRAMAN_ROSTER) >= 100
    assert len({hero.name for hero in ULTRAMAN_ROSTER}) == len(ULTRAMAN_ROSTER)
    expected_expansion = {
        "奥特之王",
        "诺亚奥特曼",
        "雷杰多奥特曼",
        "赛迦奥特曼",
        "贝利亚奥特曼",
        "极恶贝利亚",
        "闪耀迪迦",
        "闪耀赛罗",
        "泽塔奥特曼·德尔塔天爪",
    }
    assert expected_expansion <= {hero.name for hero in ULTRAMAN_ROSTER}
    for hero in ULTRAMAN_ROSTER:
        profile = ULTRAMAN_PROFILES[hero.name]
        assert profile.quote.strip()
        assert profile.description.strip()
        assert profile.background.strip()
        assert 1966 <= ULTRAMAN_DEBUT_YEARS[hero.name] <= 2026
        text = ultraman_profile_text(hero)
        assert "首次登场" in text
        assert "战士特点" in text
        assert "光之背景" in text


@pytest.mark.asyncio
async def test_official_image_bypasses_broken_legacy_host_redirect(monkeypatch):
    encoded_path = "/wp-content/uploads/%E3%83%87%E3%83%83%E3%82%AB%E3%83%BC.png"

    async def handler(request):
        if request.url.path == "/heroes/ultraman-decker":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<meta property="og:image" content="'
                    f'https://en.tsuburaya-prod.co.jp{encoded_path}">'
                ),
            )
        assert request.url.host == "tsuburaya-prod.com"
        assert request.url.raw_path.decode().startswith(encoded_path)
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"official-ultraman-image",
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(ultraman_module.httpx, "AsyncClient", mocked_client)
    result = await official_ultraman_image(
        ultraman_module.ULTRAMAN_BY_NAME["德凯奥特曼"],
        SimpleNamespace(media_timeout_seconds=10, media_max_bytes=1024),
    )

    assert base64.b64decode(result.removeprefix("base64://")) == b"official-ultraman-image"


def test_long_form_name_renders_inside_collection_card(monkeypatch):
    monkeypatch.setattr(
        ultraman_module,
        "FONT_CANDIDATES",
        ("/font-that-does-not-exist.ttc", "DejaVuSans.ttf"),
    )
    source = BytesIO()
    Image.new("RGB", (1200, 900), "navy").save(source, format="JPEG")
    hero = next(
        item for item in ULTRAMAN_ROSTER if item.name == "泽塔奥特曼·德尔塔天爪"
    )
    result = render_ultraman_card(
        hero, "base64://" + base64.b64encode(source.getvalue()).decode()
    )
    rendered = Image.open(BytesIO(base64.b64decode(result.removeprefix("base64://"))))
    assert rendered.size == (900, 1200)


def test_catalog_aliases_resolve_without_fuzzy_chat_matches():
    assert resolve_ultraman_query("奥特之父").name == "奥特之父"
    assert resolve_ultraman_query("贝利亚").name == "贝利亚奥特曼"
    assert resolve_ultraman_query("老贝").name == "贝利亚奥特曼"
    assert resolve_ultraman_query("闪耀赛罗").name == "闪耀赛罗"
    assert resolve_ultraman_query("泽塔 德尔塔天爪").name == "泽塔奥特曼·德尔塔天爪"
    assert resolve_ultraman_query("你知道贝利亚是谁吗") is None


def test_full_catalog_image_and_text_fallback_include_every_entry(monkeypatch):
    monkeypatch.setattr(
        ultraman_module,
        "FONT_CANDIDATES",
        ("/font-that-does-not-exist.ttc", "DejaVuSans.ttf"),
    )
    result = render_ultraman_catalog()
    rendered = Image.open(BytesIO(base64.b64decode(result.removeprefix("base64://"))))
    assert rendered.width == 1800
    assert rendered.height > 2000

    pages = ultraman_catalog_text_pages(max_chars=600)
    combined = "\n".join(pages)
    assert all(hero.name in combined for hero in ULTRAMAN_ROSTER)
    assert all(len(page) <= 600 for page in pages)
