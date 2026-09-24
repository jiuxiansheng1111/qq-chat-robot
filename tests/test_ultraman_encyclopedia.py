from io import BytesIO

import httpx
import pytest
from PIL import Image

import app.services.ultraman_encyclopedia as encyclopedia_module
from app.config import Settings
from app.services.ultraman_encyclopedia import (
    EncyclopediaImage,
    baidu_baike_ultraman_image,
    baidu_page_image_candidates,
    encyclopedia_reference_matches,
    encyclopedia_ultraman_image,
    moegirl_ultraman_image,
    wikipedia_ultraman_image,
)
from app.services.web_search import SearchResult


def png_bytes(width: int = 480, height: int = 720) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (88, 116, 156)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_verified_download_normalizes_supported_images_to_jpeg():
    source = png_bytes(480, 720)

    async def handler(request: httpx.Request):
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=source,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await encyclopedia_module._download_verified_image(
            client,
            "https://example.invalid/image.png",
            "https://example.invalid/page",
            Settings(_env_file=None),
        )

    import base64

    raw = base64.b64decode(result.removeprefix("base64://"))
    with Image.open(BytesIO(raw)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (480, 720)


@pytest.mark.asyncio
async def test_verified_download_rejects_black_image():
    buffer = BytesIO()
    Image.new("RGB", (480, 720), (0, 0, 0)).save(buffer, format="PNG")

    async def handler(request: httpx.Request):
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=buffer.getvalue(),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="近似全黑"):
            await encyclopedia_module._download_verified_image(
                client,
                "https://example.invalid/black.png",
                "https://example.invalid/page",
                Settings(_env_file=None),
            )


@pytest.mark.asyncio
async def test_verified_download_flattens_transparent_png_to_visible_background():
    source = Image.new("RGBA", (480, 720), (0, 0, 0, 0))
    source.paste((220, 40, 40, 255), (150, 120, 330, 650))
    buffer = BytesIO()
    source.save(buffer, format="PNG")

    async def handler(request: httpx.Request):
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=buffer.getvalue(),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await encyclopedia_module._download_verified_image(
            client,
            "https://example.invalid/transparent.png",
            "https://example.invalid/page",
            Settings(_env_file=None),
        )

    import base64

    raw = base64.b64decode(result.removeprefix("base64://"))
    with Image.open(BytesIO(raw)) as decoded:
        corner = decoded.convert("RGB").getpixel((10, 10))
        assert sum(corner) > 100


@pytest.mark.asyncio
async def test_bing_image_search_falls_back_to_thumbnail_when_original_is_blocked(
    monkeypatch,
):
    source = png_bytes(480, 720)

    async def handler(request: httpx.Request):
        if request.url.host == "www.bing.com":
            metadata = (
                '{"t":"Ultraman Geed Galaxy Rising",'
                '"desc":"Ultraman Geed Galaxy Rising",'
                '"murl":"https://blocked.example/original.png",'
                '"turl":"https://thumb.example/thumb.png",'
                '"purl":"https://example.com/geed-galaxy-rising"}'
            )
            return httpx.Response(
                200,
                text=f'<a class="iusc" m=\'{metadata}\'></a>',
            )
        if request.url.host == "blocked.example":
            return httpx.Response(403)
        if request.url.host == "thumb.example":
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=source,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(encyclopedia_module.httpx, "AsyncClient", mocked_client)

    result = await encyclopedia_module.bing_image_search_ultraman_image(
        "捷德奥特曼·银河初升",
        ("Ultraman Geed Galaxy Rising",),
        Settings(_env_file=None),
    )

    assert result is not None
    assert result.source == "Bing 缩略图精确形态匹配"


def test_baidu_standalone_form_page_can_use_exact_page_og_image():
    html = """
    <html>
      <head>
        <meta property="og:title" content="闪耀迪迦_百度百科">
        <meta property="og:image" content="https://bkimg.cdn.bcebos.com/pic/glitter.jpg">
      </head>
    </html>
    """
    candidates = baidu_page_image_candidates(
        html,
        "https://baike.baidu.com/item/闪耀迪迦/1023751",
        ("闪耀迪迦", "Glitter Tiga"),
    )
    assert candidates
    assert candidates[0][1] == "https://bkimg.cdn.bcebos.com/pic/glitter.jpg"


def test_generic_ultraman_alias_never_validates_a_different_character():
    assert encyclopedia_reference_matches(
        "初代奥特曼",
        ("奥特曼", "Ultraman"),
        "File:Ultraman Zero.png",
    ) is False
    assert encyclopedia_reference_matches(
        "初代奥特曼",
        ("奥特曼", "Ultraman"),
        "初代奥特曼",
    ) is True


def test_baidu_parent_page_accepts_raw_image_when_nearby_context_names_character_and_form():
    html = """
    <html>
      <head><meta property="og:title" content="捷德奥特曼_百度百科"></head>
      <body>
        <script>
          var text = "捷德奥特曼 尊皇形态 皇家超级大师";
          var image = "https://bkimg.cdn.bcebos.com/pic/unlabelled-royal.jpg";
        </script>
      </body>
    </html>
    """
    candidates = baidu_page_image_candidates(
        html,
        "https://baike.baidu.com/item/捷德奥特曼/20825718",
        ("捷德奥特曼·尊皇形态", "Ultraman Geed Royal Mega-Master"),
    )
    assert candidates
    assert candidates[0][1].endswith("unlabelled-royal.jpg")


def test_baidu_parent_page_requires_form_specific_image_label():
    html = """
    <html>
      <head>
        <meta property="og:title" content="捷德奥特曼_百度百科">
        <meta property="og:image" content="https://bkimg.cdn.bcebos.com/pic/geed-base.jpg">
      </head>
      <body>
        <img src="https://bkimg.cdn.bcebos.com/pic/geed-base-2.jpg" alt="捷德奥特曼 原始形态">
        <img src="https://bkimg.cdn.bcebos.com/pic/geed-galaxy.jpg" alt="捷德奥特曼 银河初升">
      </body>
    </html>
    """
    candidates = baidu_page_image_candidates(
        html,
        "https://baike.baidu.com/item/捷德奥特曼/20825718",
        ("捷德奥特曼·银河初升", "银河初升", "银河升华", "Galaxy Rising"),
    )
    urls = [item[1] for item in candidates]
    assert "https://bkimg.cdn.bcebos.com/pic/geed-galaxy.jpg" in urls
    assert "https://bkimg.cdn.bcebos.com/pic/geed-base.jpg" not in urls
    assert "https://bkimg.cdn.bcebos.com/pic/geed-base-2.jpg" not in urls


@pytest.mark.asyncio
async def test_baidu_resolver_uses_labeled_form_image_not_base_og(monkeypatch):
    async def fake_search(query: str, limit: int = 5, timeout: float = 12):
        return [
            SearchResult(
                title="捷德奥特曼_百度百科",
                url="https://bkso.baidu.com/item/捷德奥特曼/20825718",
                snippet="尊皇形态 皇家超级大师",
            )
        ]

    image_data = png_bytes()

    async def handler(request: httpx.Request):
        if request.url.host in {"baike.baidu.com", "bkso.baidu.com"}:
            return httpx.Response(
                200,
                text=(
                    '<meta property="og:title" content="捷德奥特曼_百度百科">'
                    '<meta property="og:image" '
                    'content="https://bkimg.cdn.bcebos.com/pic/geed-base.jpg">'
                    '<img src="https://bkimg.cdn.bcebos.com/pic/geed-royal.png" '
                    'alt="捷德奥特曼 尊皇形态 皇家超级大师">'
                ),
            )
        if request.url.host == "bkimg.cdn.bcebos.com":
            if request.url.path.endswith("geed-royal.png"):
                return httpx.Response(
                    200,
                    headers={"content-type": "image/png"},
                    content=image_data,
                )
            return httpx.Response(404)
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(encyclopedia_module, "search_web", fake_search)
    monkeypatch.setattr(encyclopedia_module.httpx, "AsyncClient", mocked_client)

    result = await baidu_baike_ultraman_image(
        "捷德奥特曼·尊皇形态",
        ("尊皇形态", "皇家超级大师", "Royal Mega-Master"),
        Settings(_env_file=None),
    )

    assert result is not None
    assert result.source == "百度百科"
    assert "尊皇形态" in result.label


@pytest.mark.asyncio
async def test_wikipedia_prefers_exact_embedded_form_file_over_base_pageimage(monkeypatch):
    image_data = png_bytes()

    async def handler(request: httpx.Request):
        params = request.url.params
        if request.url.host == "zh.wikipedia.org" and params.get("generator") == "search":
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": [
                            {
                                "pageid": 1,
                                "title": "超人捷德",
                                "thumbnail": {
                                    "source": "https://upload.wikimedia.org/base.jpg"
                                },
                                "images": [
                                    {"title": "File:Ultraman Geed Galaxy Rising.png"}
                                ],
                            }
                        ]
                    }
                },
            )
        if (
            request.url.host == "zh.wikipedia.org"
            and params.get("prop") == "imageinfo"
        ):
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": [
                            {
                                "pageid": 2,
                                "title": "File:Ultraman Geed Galaxy Rising.png",
                                "imageinfo": [
                                    {
                                        "thumburl": (
                                            "https://upload.wikimedia.org/"
                                            "geed-galaxy-rising.png"
                                        )
                                    }
                                ],
                            }
                        ]
                    }
                },
            )
        if request.url.host == "upload.wikimedia.org":
            assert request.url.path.endswith("geed-galaxy-rising.png")
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=image_data,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(encyclopedia_module.httpx, "AsyncClient", mocked_client)

    result = await wikipedia_ultraman_image(
        "捷德奥特曼·银河初升",
        ("Ultraman Geed Galaxy Rising", "银河初升"),
        Settings(_env_file=None),
    )

    assert result is not None
    assert "embedded image" in result.source
    assert "Galaxy Rising" in result.label


@pytest.mark.asyncio
async def test_wikipedia_rejects_base_pageimage_when_form_is_only_in_search_query(
    monkeypatch,
):
    async def handler(request: httpx.Request):
        if request.url.host in {
            "zh.wikipedia.org",
            "en.wikipedia.org",
            "ja.wikipedia.org",
            "commons.wikimedia.org",
        }:
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": [
                            {
                                "pageid": 1,
                                "title": "Ultraman Geed",
                                "thumbnail": {
                                    "source": "https://upload.wikimedia.org/base.jpg"
                                },
                                "images": [],
                            }
                        ]
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(encyclopedia_module.httpx, "AsyncClient", mocked_client)

    result = await wikipedia_ultraman_image(
        "捷德奥特曼·尊皇形态",
        ("Royal Mega-Master", "尊皇形态"),
        Settings(_env_file=None),
    )
    assert result is None


@pytest.mark.asyncio
async def test_encyclopedia_prefers_baidu_before_wikimedia(monkeypatch):
    expected = EncyclopediaImage(
        data="base64://YmFpZHU=",
        source="百度百科",
        page_url="https://baike.baidu.com/item/赛罗奥特曼",
        label="赛罗奥特曼 Zero Beyond",
    )
    calls: list[str] = []

    async def fake_baidu(name, aliases, settings):
        calls.append("baidu")
        return expected

    async def fake_wikipedia(name, aliases, settings):
        calls.append("wikipedia")
        raise AssertionError("Wikipedia should not run after a verified Baidu match")

    monkeypatch.setattr(
        encyclopedia_module,
        "wikipedia_ultraman_image",
        fake_wikipedia,
    )
    monkeypatch.setattr(
        encyclopedia_module,
        "baidu_baike_ultraman_image",
        fake_baidu,
    )

    result = await encyclopedia_ultraman_image(
        "赛罗奥特曼·无限形态",
        ("Ultraman Zero Beyond",),
        Settings(_env_file=None),
    )

    assert result == expected
    assert calls == ["baidu"]


def test_encyclopedia_reference_requires_exact_form_not_shared_suffix():
    assert encyclopedia_reference_matches(
        "迪迦奥特曼·强力型",
        ("ウルトラマンティガ パワータイプ", "Ultraman Tiga Power Type"),
        "戴拿奥特曼 强力型",
    ) is False
    assert encyclopedia_reference_matches(
        "迪迦奥特曼·强力型",
        ("ウルトラマンティガ パワータイプ", "Ultraman Tiga Power Type"),
        "File:Ultraman Tiga Power Type.png",
    ) is True


def test_compact_wikipedia_filename_without_ultraman_prefix_is_still_exact():
    assert encyclopedia_reference_matches(
        "迪迦奥特曼·强力型",
        ("ウルトラマンティガ パワータイプ", "Ultraman Tiga Power Type"),
        "File:Tiga Power Type.png",
    ) is True
    assert encyclopedia_reference_matches(
        "迪迦奥特曼·强力型",
        ("ウルトラマンティガ パワータイプ", "Ultraman Tiga Power Type"),
        "File:Dyna Strong Type.png",
    ) is False


def test_bare_shared_form_suffix_is_not_enough_to_validate_image():
    assert encyclopedia_reference_matches(
        "迪迦奥特曼·强力型",
        ("强力型", "Ultraman Tiga Power Type"),
        "强力型",
    ) is False


def test_no_id_baidu_item_page_does_not_trust_generic_og_image():
    html = """
    <html>
      <head>
        <meta property="og:title" content="赛迦奥特曼_百度百科">
        <meta property="og:image" content="https://bkimg.cdn.bcebos.com/pic/shared-placeholder.jpg">
      </head>
      <body></body>
    </html>
    """
    candidates = baidu_page_image_candidates(
        html,
        "https://bkso.baidu.com/item/赛迦奥特曼",
        ("赛迦奥特曼", "Ultraman Saga"),
    )
    assert candidates == []


def test_numeric_baidu_item_page_can_use_specific_og_image():
    html = """
    <html>
      <head>
        <meta property="og:title" content="赛迦奥特曼_百度百科">
        <meta property="og:image" content="https://bkimg.cdn.bcebos.com/pic/saga.jpg">
      </head>
      <body></body>
    </html>
    """
    candidates = baidu_page_image_candidates(
        html,
        "https://bkso.baidu.com/item/赛迦奥特曼/3008291",
        ("赛迦奥特曼", "Ultraman Saga"),
    )
    assert candidates
    assert candidates[0][1].endswith("saga.jpg")


def test_parent_hero_page_can_validate_short_geed_form_label():
    html = """
    <html>
      <head><meta property="og:title" content="捷德奥特曼_百度百科"></head>
      <body>
        <img src="https://bkimg.cdn.bcebos.com/pic/geed-primitive.jpg" alt="原始形态">
      </body>
    </html>
    """
    candidates = baidu_page_image_candidates(
        html,
        "https://bkso.baidu.com/item/捷德奥特曼/20825718",
        ("捷德奥特曼·原始形态", "Geed Primitive"),
    )
    assert candidates
    assert candidates[0][1].endswith("geed-primitive.jpg")


def test_short_form_label_is_rejected_on_wrong_parent_page():
    html = """
    <html>
      <head><meta property="og:title" content="欧布奥特曼_百度百科"></head>
      <body>
        <img src="https://bkimg.cdn.bcebos.com/pic/orb-something.jpg" alt="原始形态">
      </body>
    </html>
    """
    candidates = baidu_page_image_candidates(
        html,
        "https://bkso.baidu.com/item/欧布奥特曼/19876223",
        ("捷德奥特曼·原始形态", "Geed Primitive"),
    )
    assert candidates == []


def test_tiga_power_short_label_requires_tiga_parent_page():
    tiga_html = """
    <html>
      <head><meta property="og:title" content="迪迦奥特曼_百度百科"></head>
      <body>
        <img src="https://bkimg.cdn.bcebos.com/pic/tiga-power.jpg" alt="强力型">
      </body>
    </html>
    """
    accepted = baidu_page_image_candidates(
        tiga_html,
        "https://bkso.baidu.com/item/迪迦奥特曼/21810",
        ("迪迦奥特曼·强力型", "Tiga Power Type"),
    )
    assert accepted

    dyna_html = tiga_html.replace("迪迦奥特曼", "戴拿奥特曼")
    rejected = baidu_page_image_candidates(
        dyna_html,
        "https://bkso.baidu.com/item/戴拿奥特曼/24257648",
        ("迪迦奥特曼·强力型", "Tiga Power Type"),
    )
    assert rejected == []


def test_wikipedia_wikitext_candidate_uses_form_caption():
    text = """
    == Forms ==
    [[File:Tiga costume red.jpg|thumb|Ultraman Tiga Power Type]]
    [[File:Tiga costume purple.jpg|thumb|Ultraman Tiga Sky Type]]
    """
    candidates = encyclopedia_module._wikipedia_wikitext_image_candidates(
        text,
        "迪迦奥特曼·强力型",
        ("ウルトラマンティガ パワータイプ", "Ultraman Tiga Power Type"),
        "Ultraman Tiga (character)",
    )
    assert candidates == ["File:Tiga costume red.jpg"]


def test_wikipedia_wikitext_candidate_rejects_other_form_on_same_parent():
    text = """
    == Forms ==
    [[File:Tiga power.jpg|thumb|Ultraman Tiga Power Type]]
    [[File:Tiga sky.jpg|thumb|Ultraman Tiga Sky Type]]
    """
    candidates = encyclopedia_module._wikipedia_wikitext_image_candidates(
        text,
        "迪迦奥特曼·空中型",
        ("ウルトラマンティガ スカイタイプ", "Ultraman Tiga Sky Type"),
        "Ultraman Tiga (character)",
    )
    assert candidates == ["File:Tiga sky.jpg"]


@pytest.mark.asyncio
async def test_baidu_image_fallback_requires_trusted_exact_source(monkeypatch):
    image_data = png_bytes()

    async def handler(request: httpx.Request):
        if request.url.host == "image.baidu.com" and request.url.path == "/search/acjson":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "fromURLHost": "random-wallpaper.example",
                            "fromPageTitleEnc": "迪迦奥特曼 强力型",
                            "middleURL": "https://image.baidu.com/random.jpg",
                        },
                        {
                            "fromURLHost": "bkso.baidu.com",
                            "fromPageTitleEnc": "迪迦奥特曼 强力型",
                            "middleURL": "https://image.baidu.com/tiga-power.jpg",
                            "fromURL": "https://bkso.baidu.com/item/迪迦奥特曼",
                        },
                    ]
                },
            )
        if request.url.host == "image.baidu.com" and request.url.path.endswith(".jpg"):
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=image_data,
            )
        if request.url.path.endswith("tiga-power.jpg"):
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=image_data,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(encyclopedia_module.httpx, "AsyncClient", mocked_client)

    result = await encyclopedia_module.baidu_image_search_ultraman_image(
        "迪迦奥特曼·强力型",
        ("迪迦强力型", "Ultraman Tiga Power Type"),
        Settings(_env_file=None),
    )
    assert result is not None
    assert result.source.startswith("百度图片")
    assert "强力型" in result.label


def test_bing_image_parser_extracts_exact_tile_metadata():
    parser = encyclopedia_module._BingImageResultParser()
    parser.feed(
        '<a class="iusc" m="{&quot;murl&quot;:&quot;https://img.example/geed.jpg&quot;,'
        '&quot;purl&quot;:&quot;https://example.com/geed&quot;,'
        '&quot;t&quot;:&quot;捷德奥特曼·刚燃形态&quot;}"></a>'
    )
    assert len(parser.items) == 1
    assert parser.items[0]["murl"] == "https://img.example/geed.jpg"
    assert parser.items[0]["t"] == "捷德奥特曼·刚燃形态"


@pytest.mark.asyncio
async def test_bing_image_fallback_skips_wrong_form_and_accepts_exact_form(monkeypatch):
    image_data = png_bytes()
    wrong_metadata = (
        '{"murl":"https://img.example/geed-primitive.jpg",'
        '"purl":"https://example.com/primitive",'
        '"t":"捷德奥特曼·原始形态"}'
    )
    exact_metadata = (
        '{"murl":"https://img.example/geed-solid-burning.jpg",'
        '"purl":"https://example.com/solid-burning",'
        '"t":"捷德奥特曼·刚燃形态"}'
    )
    bing_html = (
        '<a class="iusc" m="' + wrong_metadata.replace('"', '&quot;') + '"></a>'
        '<a class="iusc" m="' + exact_metadata.replace('"', '&quot;') + '"></a>'
    )

    async def handler(request: httpx.Request):
        if request.url.host == "www.bing.com":
            return httpx.Response(200, text=bing_html)
        if request.url.host == "img.example":
            assert request.url.path.endswith("geed-solid-burning.jpg")
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=image_data,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(encyclopedia_module.httpx, "AsyncClient", mocked_client)

    result = await encyclopedia_module.bing_image_search_ultraman_image(
        "捷德奥特曼·刚燃形态",
        ("捷德刚燃形态", "坚固燃烧", "Geed Solid Burning"),
        Settings(_env_file=None),
    )
    assert result is not None
    assert result.source.startswith("Bing")
    assert result.label == "捷德奥特曼·刚燃形态"


@pytest.mark.asyncio
async def test_search_engine_first_image_skips_wrong_form_metadata(monkeypatch):
    image_data = png_bytes()

    async def handler(request: httpx.Request):
        if request.url.host == "image.baidu.com" and request.url.path == "/search/acjson":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "fromURLHost": "example.com",
                            "fromPageTitleEnc": "捷德奥特曼·原始形态",
                            "middleURL": "https://img.example/wrong.jpg",
                            "fromURL": "https://example.com/geed-primitive",
                        },
                        {
                            "fromURLHost": "example.com",
                            "fromPageTitleEnc": "捷德奥特曼·刚燃形态",
                            "middleURL": "https://img.example/exact.jpg",
                            "fromURL": "https://example.com/geed-solid-burning",
                        },
                    ]
                },
            )
        if request.url.host == "img.example" and request.url.path == "/exact.jpg":
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=image_data,
            )
        if request.url.host == "img.example" and request.url.path == "/wrong.jpg":
            raise AssertionError("wrong form image must not be downloaded")
        if request.url.host == "www.bing.com":
            return httpx.Response(200, text="")
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(encyclopedia_module.httpx, "AsyncClient", mocked_client)
    result = await encyclopedia_module.search_engine_first_ultraman_image(
        "捷德奥特曼·刚燃形态",
        ("Ultraman Geed Solid Burning",),
        Settings(_env_file=None),
    )
    assert result is not None
    assert "刚燃形态" in result.label



@pytest.mark.asyncio
async def test_moegirl_ultraman_source_accepts_exact_character_page(monkeypatch):
    image_data = png_bytes()

    async def handler(request: httpx.Request):
        if request.url.path.endswith("/api.php"):
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": {
                            "42": {
                                "pageid": 42,
                                "title": "迪迦奥特曼·强力型",
                                "original": {
                                    "source": "https://img.moegirl.example/tiga-power.png"
                                },
                            }
                        }
                    }
                },
            )
        if request.url.host == "img.moegirl.example":
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=image_data,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(encyclopedia_module.httpx, "AsyncClient", mocked_client)
    result = await moegirl_ultraman_image(
        "迪迦奥特曼·强力型",
        ("ウルトラマンティガ パワータイプ", "Ultraman Tiga Power Type"),
        Settings(_env_file=None),
    )
    assert result is not None
    assert result.source == "萌娘百科"
    assert "强力型" in result.label


@pytest.mark.asyncio
async def test_moegirl_ultraman_source_rejects_parent_page_for_specific_form(monkeypatch):
    async def handler(request: httpx.Request):
        if request.url.path.endswith("/api.php"):
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": {
                            "42": {
                                "pageid": 42,
                                "title": "迪迦奥特曼",
                                "original": {
                                    "source": "https://img.moegirl.example/base.png"
                                },
                            }
                        }
                    }
                },
            )
        raise AssertionError("base image must never be downloaded for a form")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(encyclopedia_module.httpx, "AsyncClient", mocked_client)
    result = await moegirl_ultraman_image(
        "迪迦奥特曼·强力型",
        ("ウルトラマンティガ パワータイプ", "Ultraman Tiga Power Type"),
        Settings(_env_file=None),
    )
    assert result is None
