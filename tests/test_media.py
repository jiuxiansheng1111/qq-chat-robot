import asyncio
import base64
from types import SimpleNamespace

import httpx
import pytest

import app.plugins.media as media_module
from app.plugins.media import (
    random_cat_gif,
    random_image,
    random_nailong_image,
    random_real_pig_image,
)


@pytest.mark.asyncio
async def test_cataas_returns_gif_only(monkeypatch):
    async def handler(request):
        assert request.url.path == "/cat/gif"
        assert request.headers["cache-control"] == "no-cache"
        return httpx.Response(
            200,
            headers={"content-type": "image/gif"},
            content=b"GIF89a-cat",
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    result = await random_image(
        "https://cataas.com/cat/gif",
        "",
        SimpleNamespace(media_max_bytes=5 * 1024 * 1024),
    )
    assert base64.b64decode(result.removeprefix("base64://")) == b"GIF89a-cat"


@pytest.mark.asyncio
async def test_cat_gif_uses_warmed_cache(monkeypatch):
    media_module._cat_gif_cache.clear()
    media_module._cat_gif_cache.append("base64://cached-cat")

    async def no_refill(settings):
        return None

    monkeypatch.setattr(media_module, "warm_cat_gif_cache", no_refill)
    result = await random_cat_gif(SimpleNamespace(cat_cache_size=2))
    await asyncio.sleep(0)
    assert result == "base64://cached-cat"


@pytest.mark.asyncio
async def test_cat_gif_retries_transient_server_error(monkeypatch):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, headers={"content-type": "text/plain"})
        return httpx.Response(
            200,
            headers={"content-type": "image/gif"},
            content=b"GIF89a-retried-cat",
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    result = await media_module._download_cat_gif(
        SimpleNamespace(
            cat_api_url="https://cataas.com/cat/gif",
            cat_timeout_seconds=5,
            media_max_bytes=1024,
            media_retry_attempts=2,
        )
    )
    assert calls == 2
    assert base64.b64decode(result.removeprefix("base64://")) == b"GIF89a-retried-cat"


@pytest.mark.asyncio
async def test_random_nailong_image_downloads_curated_asset(monkeypatch):
    async def fixed_path():
        return "gif/example.gif"

    async def handler(request):
        assert request.url.path.endswith("/gif/example.gif")
        return httpx.Response(
            200,
            headers={"content-type": "image/gif"},
            content=b"GIF89a-nailong",
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    monkeypatch.setattr(media_module, "_next_nailong_path", fixed_path)
    result = await random_nailong_image(
        SimpleNamespace(
            media_timeout_seconds=10,
            media_retry_attempts=1,
            media_max_bytes=5 * 1024 * 1024,
        )
    )

    assert base64.b64decode(result.removeprefix("base64://")) == b"GIF89a-nailong"


@pytest.mark.asyncio
async def test_real_pig_image_uses_curated_wikimedia_photo(monkeypatch):
    async def fixed_title():
        return "File:Cute Piglet.jpg"

    async def handler(request):
        assert request.headers["user-agent"].startswith("qq-chatrobot/")
        if request.url.host == "upload.wikimedia.org":
            return httpx.Response(
                200,
                headers={"content-type": "image/jpeg"},
                content=b"real-pig-photo",
            )
        assert request.url.params["titles"] == "File:Cute Piglet.jpg"
        return httpx.Response(
            200,
            json={
                "query": {
                    "pages": [
                        {
                            "imageinfo": [
                                {
                                    "thumburl": "https://upload.wikimedia.org/cute-piglet.jpg",
                                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Cute_Piglet.jpg",
                                }
                            ]
                        }
                    ]
                }
            },
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    monkeypatch.setattr(media_module, "_next_pig_title", fixed_title)
    result = await random_real_pig_image(
        "https://commons.wikimedia.org/w/api.php",
        SimpleNamespace(
            media_timeout_seconds=10,
            media_retry_attempts=1,
            media_max_bytes=5 * 1024 * 1024,
        ),
    )

    assert result.url.startswith("base64://")
    assert base64.b64decode(result.url.removeprefix("base64://")) == b"real-pig-photo"
    assert result.source_url.endswith("File:Cute_Piglet.jpg")



@pytest.mark.asyncio
async def test_cat_gif_retries_recent_duplicate_content(monkeypatch):
    media_module._cat_gif_cache.clear()
    media_module._cat_cached_hashes.clear()
    media_module._cat_recent_hashes.clear()

    duplicate = "base64://" + base64.b64encode(b"GIF89a-same-cat").decode()
    unique = "base64://" + base64.b64encode(b"GIF89a-new-cat").decode()
    media_module._remember_cat_digest(media_module._cat_digest(duplicate))
    values = iter((duplicate, unique))

    async def fake_download(settings):
        return next(values)

    async def no_refill(settings):
        return None

    monkeypatch.setattr(media_module, "_download_cat_gif", fake_download)
    monkeypatch.setattr(media_module, "warm_cat_gif_cache", no_refill)

    result = await random_cat_gif(SimpleNamespace(cat_cache_size=6))
    await asyncio.sleep(0)
    assert result == unique
