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
    ultraman_image_aliases,
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
        "赛罗奥特曼·闪耀型",
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
    assert resolve_ultraman_query("赛罗奥特曼·闪耀型").name == "赛罗奥特曼·闪耀型"
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
        "欧布奥特曼·重光形态",
        "欧布奥特曼·暴炎形态",
        "欧布奥特曼·疾风形态",
        "欧布奥特曼·暗耀形态",
        "欧布奥特曼·原生形态",
        "欧布奥特曼·煌闪形态",
        "欧布奥特曼·智勇形态",
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
        "梦比优斯奥特曼·无限形态",
        # 真正的奥特战士合体/融合战士
        "超级奥特曼泰罗",
        "雷杰多奥特曼",
        "赛迦奥特曼",
        "银河维克特利奥特曼",
        "罗布奥特曼",
        "格罗布奥特曼",
        "泰迦奥特曼·三重斯特利姆形态",
        "令迦奥特曼",
        "真理特利迦",
        # 2025-2026 TV形态
        "欧米伽奥特曼·雷金斯装甲",
        "欧米伽奥特曼·特里加隆装甲",
        "欧米伽奥特曼·瓦尔根斯装甲",
        "欧米伽奥特曼·加梅顿装甲",
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


def test_every_form_has_official_alias_metadata_and_resolves_back():
    form_names = {item[0] for item in ultraman_module._FORM_VARIANTS}
    assert set(ultraman_module._FORM_ALT_NAMES) == form_names

    for canonical_name, aliases in ultraman_module._FORM_ALT_NAMES.items():
        assert aliases
        assert all(alias.strip() for alias in aliases)
        for alias in aliases:
            resolved = resolve_ultraman_query(alias)
            assert resolved is not None
            assert resolved.name == canonical_name


def test_audited_legacy_names_still_resolve_to_new_display_names():
    expected = {
        "梦比优斯无限形态": "梦比优斯奥特曼·无限形态",
        "强壮日冕赛罗": "赛罗奥特曼·强壮日冕型",
        "月神奇迹赛罗": "赛罗奥特曼·月神奇迹型",
        "闪耀赛罗": "赛罗奥特曼·闪耀型",
        "赛罗奥特曼 超越形态": "赛罗奥特曼·无限形态",
        "银河斯特利姆": "银河奥特曼·斯特利姆形态",
        "银河维克特利": "银河维克特利奥特曼",
        "艾克斯奥特曼 超越型": "艾克斯奥特曼·超越形态",
        "欧布奥特曼 斯佩修姆哉佩利敖": "欧布奥特曼·重光形态",
        "欧布奥特曼 燃烧炸弹": "欧布奥特曼·暴炎形态",
        "欧布奥特曼 闪电攻击者": "欧布奥特曼·煌闪形态",
        "欧布奥特曼 艾梅利姆头镖": "欧布奥特曼·智勇形态",
        "泰迦奥特曼 三重斯特利姆": "泰迦奥特曼·三重斯特利姆形态",
        "特利迦真理形态": "真理特利迦",
        "布莱泽奥特曼 法德兰装甲": "布莱泽奥特曼·法多兰盔甲",
        "亚刻奥特曼 太阳装甲": "亚刻奥特曼·索利斯装甲",
        "亚刻奥特曼 月亮装甲": "亚刻奥特曼·露娜装甲",
        "欧米伽奥特曼 雷基尼斯装甲": "欧米伽奥特曼·雷金斯装甲",
        "欧米伽奥特曼 瓦尔格尼斯装甲": "欧米伽奥特曼·瓦尔根斯装甲",
        "欧米伽奥特曼 盖梅顿装甲": "欧米伽奥特曼·加梅顿装甲",
    }
    for alias, canonical_name in expected.items():
        resolved = resolve_ultraman_query(alias)
        assert resolved is not None
        assert resolved.name == canonical_name


def test_orb_official_chinese_aliases_are_not_cross_wired():
    assert resolve_ultraman_query("欧布智勇形态").name == "欧布奥特曼·智勇形态"
    assert resolve_ultraman_query("艾梅利姆头镖").name == "欧布奥特曼·智勇形态"
    assert resolve_ultraman_query("欧布煌闪形态").name == "欧布奥特曼·煌闪形态"
    assert resolve_ultraman_query("闪电攻击者").name == "欧布奥特曼·煌闪形态"


def test_related_special_forms_also_require_specific_artwork():
    expected = {
        "帝纳斯奥特曼",
        "诺亚奥特曼",
        "雷杰多奥特曼",
        "赛迦奥特曼",
        "贝利亚早期形态",
        "托雷基亚早期形态",
    }
    assert ultraman_module._RELATED_VARIANT_NAMES == expected
    for name in expected:
        hero = ultraman_module.ULTRAMAN_BY_NAME[name]
        assert is_ultraman_form_variant(hero)

    for canonical_name, aliases in ultraman_module._RELATED_ALT_NAMES.items():
        for alias in aliases:
            resolved = resolve_ultraman_query(alias)
            assert resolved is not None
            assert resolved.name == canonical_name


def test_base_image_aliases_include_slug_but_forms_do_not_inherit_parent_slug():
    zero = ultraman_module.ULTRAMAN_BY_NAME["赛罗奥特曼"]
    zero_beyond = ultraman_module.ULTRAMAN_BY_NAME["赛罗奥特曼·无限形态"]

    assert "ultraman zero" in ultraman_image_aliases(zero)
    assert "ultraman zero" not in ultraman_image_aliases(zero_beyond)
    assert "Ultraman Zero Beyond" in ultraman_image_aliases(zero_beyond)


@pytest.mark.asyncio
async def test_saga_never_uses_movie_page_poster_as_character_art(monkeypatch):
    async def handler(request: httpx.Request):
        if request.url.path == "/business/titlelist/8015":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<meta property="og:image" '
                    'content="https://tsuburaya-prod.com/uploads/ultraman-saga-movie-poster.jpg">'
                ),
            )
        raise AssertionError(f"unexpected image request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(ultraman_module.httpx, "AsyncClient", mocked_client)
    hero = ultraman_module.ULTRAMAN_BY_NAME["赛迦奥特曼"]
    assert is_ultraman_form_variant(hero)

    with pytest.raises(RuntimeError, match="没有返回可用图片"):
        await official_ultraman_image(
            hero,
            SimpleNamespace(media_timeout_seconds=10, media_max_bytes=1024),
        )


def test_verified_direct_image_map_only_contains_specific_roster_entries():
    roster_names = {hero.name for hero in ULTRAMAN_ROSTER}
    assert set(ultraman_module._FORM_IMAGE_DIRECT_URLS) <= roster_names
    for name, url in ultraman_module._FORM_IMAGE_DIRECT_URLS.items():
        hero = ultraman_module.ULTRAMAN_BY_NAME[name]
        assert is_ultraman_form_variant(hero)
        assert url.startswith("https://tsuburaya-prod.com/wp-content/uploads/")


def test_form_image_search_is_specific_for_every_form():
    for form_name, *_ in ultraman_module._FORM_VARIANTS:
        hero = ultraman_module.ULTRAMAN_BY_NAME[form_name]
        query = ultraman_image_search_query(hero)
        assert query.strip()
        base_name = form_name.split("·", 1)[0]
        assert base_name in query or form_name in query


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
    assert resolve_ultraman_query("闪电攻击者").name == "欧布奥特曼·煌闪形态"
    assert resolve_ultraman_query("格罗布").name == "格罗布奥特曼"
    assert resolve_ultraman_query("雷基尼斯装甲").name == "欧米伽奥特曼·雷金斯装甲"
    assert resolve_ultraman_query("盖梅顿装甲").name == "欧米伽奥特曼·加梅顿装甲"


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
    hero = ultraman_module.ULTRAMAN_BY_NAME["赛罗奥特曼·闪耀型"]
    assert is_ultraman_form_variant(hero)

    with pytest.raises(RuntimeError, match="没有返回可用图片"):
        await official_ultraman_image(
            hero,
            SimpleNamespace(media_timeout_seconds=10, media_max_bytes=1024),
        )


def test_every_form_has_strong_formal_image_aliases():
    form_names = {item[0] for item in ultraman_module._FORM_VARIANTS}
    assert form_names == set(ultraman_module._FORM_ALT_NAMES)
    assert ultraman_module._RELATED_VARIANT_NAMES <= set(ultraman_module._RELATED_ALT_NAMES)

    for name in sorted(form_names | ultraman_module._RELATED_VARIANT_NAMES):
        hero = ultraman_module.ULTRAMAN_BY_NAME[name]
        aliases = ultraman_image_aliases(hero)
        assert aliases[0] == name
        assert len(aliases) >= 2
        assert any(
            any("\u3040" <= ch <= "\u30ff" for ch in alias)
            or any("a" <= ch.casefold() <= "z" for ch in alias)
            for alias in aliases[1:]
        ), name


def test_variant_image_aliases_do_not_inherit_chat_only_parent_or_bare_suffixes():
    for form_name, *_ in ultraman_module._FORM_VARIANTS:
        hero = ultraman_module.ULTRAMAN_BY_NAME[form_name]
        aliases = ultraman_image_aliases(hero)
        parent_slug = hero.slug.replace("-", " ").casefold()
        assert parent_slug not in {alias.casefold() for alias in aliases}
        if "·" in form_name:
            bare_suffix = form_name.split("·", 1)[1]
            if bare_suffix not in ultraman_module._ENCYCLOPEDIA_IMAGE_ALIASES.get(form_name, ()):
                assert bare_suffix not in aliases


def test_official_form_match_rejects_another_hero_with_same_generic_form_word():
    tiga_power = ultraman_module.ULTRAMAN_BY_NAME["迪迦奥特曼·强力型"]
    assert not ultraman_module._official_form_image_matches(
        tiga_power,
        "https://example.invalid/ultraman-dyna-strong-type.jpg",
        "戴拿奥特曼 强力型",
    )
    assert ultraman_module._official_form_image_matches(
        tiga_power,
        "https://example.invalid/ultraman-tiga-power-type.jpg",
        "Ultraman Tiga Power Type",
    )


def test_every_variant_search_query_is_specific_to_that_variant():
    for name in sorted(
        ultraman_module._FORM_VARIANT_NAMES | ultraman_module._RELATED_VARIANT_NAMES
    ):
        hero = ultraman_module.ULTRAMAN_BY_NAME[name]
        query = ultraman_image_search_query(hero)
        aliases = ultraman_image_aliases(hero)
        normalized_query = ultraman_module._normalize_image_descriptor(query)
        assert any(
            ultraman_module._normalize_image_descriptor(alias) in normalized_query
            or normalized_query in ultraman_module._normalize_image_descriptor(alias)
            for alias in aliases
            if alias
        ), name
