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
    wikipedia_ultraman_image,
)
from app.services.web_search import SearchResult


def png_bytes(width: int = 480, height: int = 720) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height)).save(buffer, format="PNG")
    return buffer.getvalue()


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
        if request.url.host == "bkso.baidu.com":
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
