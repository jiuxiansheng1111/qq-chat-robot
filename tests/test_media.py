import asyncio
import base64
import io
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

import app.plugins.media as media_module
from app.plugins.media import (
    random_cat_gif,
    random_image,
    random_nailong_image,
    random_real_pig_image,
)
from app.services.cat_history import (
    CatHistoryError,
    CatImageHistory,
    NoNewCatImage,
    fingerprint_cat_gif,
)


def cat_image(color='red'):
    output = io.BytesIO()
    Image.new('RGB', (3, 3), color).save(
        output, format='GIF', save_all=True,
        append_images=[Image.new('RGB', (3, 3), 'blue')], duration=100, loop=0,
    )
    return 'base64://' + base64.b64encode(output.getvalue()).decode()


@pytest.fixture(autouse=True)
def clear_cat_cache():
    media_module._cat_gif_cache.clear()
    media_module._cat_cached_hashes.clear()
    yield
    media_module._cat_gif_cache.clear()
    media_module._cat_cached_hashes.clear()


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
    cached = cat_image()
    media_module._cat_gif_cache.append(cached)

    async def no_refill(settings):
        return None

    monkeypatch.setattr(media_module, "warm_cat_gif_cache", no_refill)
    result = await random_cat_gif(SimpleNamespace(cat_cache_size=2))
    await asyncio.sleep(0)
    assert result == cached


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
    duplicate = cat_image('red')
    unique = cat_image('green')
    await CatImageHistory.from_settings(SimpleNamespace()).claim(
        fingerprint_cat_gif(media_module._cat_content(duplicate)),
    )
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


@pytest.mark.asyncio
async def test_cat_gif_does_not_send_repeated_content_when_upstream_is_stuck(monkeypatch):
    media_module._cat_gif_cache.clear()
    duplicate = cat_image()
    await CatImageHistory.from_settings(SimpleNamespace()).claim(
        fingerprint_cat_gif(media_module._cat_content(duplicate)),
    )

    async def fake_download(settings):
        return duplicate

    monkeypatch.setattr(media_module, '_download_cat_gif', fake_download)
    with pytest.raises(RuntimeError, match='重复图片'):
        await random_cat_gif(SimpleNamespace(cat_cache_size=6))


@pytest.mark.asyncio
async def test_giphy_and_cached_or_downloaded_fallback_share_permanent_history(monkeypatch):
    from app.services import giphy_cat

    duplicate = cat_image('red')
    unique = cat_image('green')
    media_module._cat_gif_cache.append(duplicate)
    monkeypatch.setattr(giphy_cat, '_PAGE_CANDIDATES', {})
    monkeypatch.setattr(giphy_cat, '_IN_FLIGHT_IDS', set())

    def handler(request):
        if request.url.host == 'giphy.com':
            return httpx.Response(200, text=(
                '<meta property="og:image" '
                'content="https://media.giphy.com/media/history-cat/giphy.gif">'
            ))
        return httpx.Response(200, content=media_module._cat_content(duplicate))

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        **(kwargs | {"transport": httpx.MockTransport(handler)}),
    ))
    downloads = iter((duplicate, unique))

    async def download(_):
        return next(downloads)

    async def no_refill(_):
        pass

    monkeypatch.setattr(media_module, '_download_cat_gif', download)
    monkeypatch.setattr(media_module, 'warm_cat_gif_cache', no_refill)
    settings = SimpleNamespace(cat_giphy_enabled=True, media_max_bytes=4096)
    assert await random_cat_gif(settings) == duplicate
    assert await random_cat_gif(settings) == unique
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_parallel_cat_requests_cannot_return_the_same_cached_or_downloaded_gif(monkeypatch):
    image = cat_image()
    media_module._cat_gif_cache.extend([image] * 4)

    async def download(_):
        return image

    async def no_refill(_):
        pass

    monkeypatch.setattr(media_module, '_download_cat_gif', download)
    monkeypatch.setattr(media_module, 'warm_cat_gif_cache', no_refill)
    results = await asyncio.gather(*[
        random_cat_gif(SimpleNamespace()) for _ in range(4)
    ], return_exceptions=True)
    assert results.count(image) == 1
    assert sum(isinstance(result, NoNewCatImage) for result in results) == 3


@pytest.mark.asyncio
async def test_cache_warming_does_not_reserve_unsent_images(monkeypatch):
    image = cat_image()

    async def download(_):
        return image

    monkeypatch.setattr(media_module, '_download_cat_gif', download)
    settings = SimpleNamespace(cat_cache_size=1)
    await media_module.warm_cat_gif_cache(settings)
    fingerprint = fingerprint_cat_gif(media_module._cat_content(image))
    history = CatImageHistory.from_settings(settings)
    assert not await history.contains(fingerprint)

    async def no_refill(_):
        pass

    monkeypatch.setattr(media_module, 'warm_cat_gif_cache', no_refill)
    assert await random_cat_gif(settings) == image
    assert await history.contains(fingerprint)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_history_failure_does_not_bypass_guard_by_switching_provider(monkeypatch):
    from unittest.mock import AsyncMock

    primary = AsyncMock(side_effect=CatHistoryError('history unavailable'))
    fallback = AsyncMock()
    monkeypatch.setattr(media_module, 'random_giphy_cat_gif', primary)
    monkeypatch.setattr(media_module, '_download_cat_gif', fallback)
    with pytest.raises(CatHistoryError):
        await random_cat_gif(SimpleNamespace(cat_giphy_enabled=True))
    fallback.assert_not_awaited()
