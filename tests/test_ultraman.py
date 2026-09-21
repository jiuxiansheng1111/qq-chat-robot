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
    is_ultraman_form_variant,
    official_ultraman_image,
    render_ultraman_card,
    render_ultraman_catalog,
    resolve_ultraman_query,
    ultraman_catalog_text_pages,
    ultraman_image_search_query,
    ultraman_profile_text,
)


def test_every_ultraman_has_complete_collection_card_content():
    assert len(ULTRAMAN_ROSTER) >= 120
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
    assert resolve_ultraman_query("泽塔德尔塔天爪？").name == "泽塔奥特曼·德尔塔天爪"
    assert resolve_ultraman_query("介绍一下贝利亚").name == "贝利亚奥特曼"
    assert resolve_ultraman_query("看看奥特之父").name == "奥特之父"
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


def test_tv_forms_and_true_fusion_forms_only():
    names = {hero.name for hero in ULTRAMAN_ROSTER}

    required = {
        # 欧布：TV/电视特别篇实际登场形态
        "欧布奥特曼·斯佩修姆哉佩利敖",
        "欧布奥特曼·燃烧炸弹",
        "欧布奥特曼·疾风形态",
        "欧布奥特曼·暗耀形态",
        "欧布奥特曼·原生形态",
        "欧布奥特曼·闪电攻击者",
        "欧布奥特曼·艾梅利姆头镖",
        # 捷德：TV正剧及《泽塔奥特曼》TV客串形态
        "捷德奥特曼·原始形态",
        "捷德奥特曼·刚燃形态",
        "捷德奥特曼·机敏形态",
        "捷德奥特曼·豪勇形态",
        "捷德奥特曼·尊皇形态",
        "捷德奥特曼·银河初升",
        # 梦比优斯：TV形态 + 真正的奥特战士合体形态
        "梦比优斯奥特曼·勇者形态",
        "梦比优斯奥特曼·燃烧勇者",
        "梦比优斯奥特曼·凤凰勇者",
        "梦比优斯无限形态",
        # 真正的奥特战士合体/融合战士
        "超级奥特曼泰罗",
        "雷杰多奥特曼",
        "赛迦奥特曼",
        "银河维克特利",
        "罗布奥特曼",
        "格罗布奥特曼",
        "泰迦奥特曼·三重斯特利姆",
        "令迦奥特曼",
        "特利迦真理形态",
        # 2025-2026 TV形态
        "欧米伽奥特曼·雷基尼斯装甲",
        "欧米伽奥特曼·特里加隆装甲",
        "欧米伽奥特曼·瓦尔格尼斯装甲",
        "欧米伽奥特曼·盖梅顿装甲",
    }
    forbidden = {
        # 舞台 / 电影专属非合体 / 街机游戏专属
        "盖亚奥特曼·超级至高型",
        "欧布奥特曼·初始形态",
        "欧布奥特曼·三位一体",
        "欧布奥特曼·光子维克特利姆",
        "欧布奥特曼·满月扎纳帝姆",
        "捷德奥特曼·初始形态",
        "捷德奥特曼·终极形态",
        "捷德奥特曼·闪耀神秘",
        "捷德奥特曼·三重头镖",
        "捷德奥特曼·无限交叉",
        "贝塔火花艾克斯",
    }

    assert required <= names
    assert forbidden.isdisjoint(names)


def test_orb_dark_form_image_search_uses_common_chinese_alias():
    hero = ultraman_module.ULTRAMAN_BY_NAME["欧布奥特曼·暗耀形态"]
    query = ultraman_image_search_query(hero)
    assert "欧布奥特曼" in query
    assert "暗耀形态" in query


def test_tv_form_aliases_resolve_to_canonical_entries():
    assert resolve_ultraman_query("欧布原生").name == "欧布奥特曼·原生形态"
    assert resolve_ultraman_query("欧布奥特曼 暗耀形态").name == "欧布奥特曼·暗耀形态"
    assert resolve_ultraman_query("欧布暗耀").name == "欧布奥特曼·暗耀形态"
    assert resolve_ultraman_query("暗耀形态").name == "欧布奥特曼·暗耀形态"
    assert resolve_ultraman_query("雷霆胸章").name == "欧布奥特曼·暗耀形态"
    assert resolve_ultraman_query("Thunder Breastar").name == "欧布奥特曼·暗耀形态"
    assert resolve_ultraman_query("サンダーブレスター").name == "欧布奥特曼·暗耀形态"
    assert resolve_ultraman_query("闪电攻击者").name == "欧布奥特曼·闪电攻击者"
    assert resolve_ultraman_query("格罗布").name == "格罗布奥特曼"
    assert resolve_ultraman_query("雷基尼斯装甲").name == "欧米伽奥特曼·雷基尼斯装甲"
    assert resolve_ultraman_query("盖梅顿装甲").name == "欧米伽奥特曼·盖梅顿装甲"


@pytest.mark.asyncio
async def test_shining_tiga_uses_dedicated_official_store_page(monkeypatch):
    requested: list[str] = []

    async def handler(request: httpx.Request):
        requested.append(str(request.url))
        if request.url.host == "store.m-78.jp":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<meta property="og:image" '
                    'content="https://cdn.shopify.com/s/files/glitter-tiga.jpg">'
                ),
            )
        assert request.url.host == "cdn.shopify.com"
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=b"glitter-tiga-image",
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(ultraman_module.httpx, "AsyncClient", mocked_client)
    hero = ultraman_module.ULTRAMAN_BY_NAME["闪耀迪迦"]

    result = await official_ultraman_image(
        hero,
        SimpleNamespace(media_timeout_seconds=10, media_max_bytes=1024),
    )

    assert is_ultraman_form_variant(hero)
    assert any("store.m-78.jp" in url for url in requested)
    assert not any("/heroes/ultraman-tiga" in url for url in requested)
    assert base64.b64decode(result.removeprefix("base64://")) == b"glitter-tiga-image"


@pytest.mark.asyncio
async def test_form_without_exact_image_never_falls_back_to_base_art(monkeypatch):
    async def handler(request: httpx.Request):
        if request.url.path == "/heroes/ultraman-zero":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<meta property="og:image" '
                    'content="https://tsuburaya-prod.com/uploads/ordinary-zero.jpg">'
                ),
            )
        raise AssertionError(f"unexpected image request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(ultraman_module.httpx, "AsyncClient", mocked_client)
    hero = ultraman_module.ULTRAMAN_BY_NAME["闪耀赛罗"]
    assert is_ultraman_form_variant(hero)

    with pytest.raises(RuntimeError, match="没有返回可用图片"):
        await official_ultraman_image(
            hero,
            SimpleNamespace(media_timeout_seconds=10, media_max_bytes=1024),
        )
