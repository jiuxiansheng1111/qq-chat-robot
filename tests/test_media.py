import base64
from types import SimpleNamespace

import httpx
import pytest

import app.plugins.media as media_module
from app.plugins.media import random_image, random_real_pig_image


@pytest.mark.asyncio
async def test_the_cat_api_json_array(monkeypatch):
    async def handler(request):
        assert request.headers["x-api-key"] == "cat-key"
        return httpx.Response(200, json=[{"id": "abc", "url": "https://cdn.example/cat.jpg"}])

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    result = await random_image(
        "https://api.thecatapi.com/v1/images/search",
        "cat-key",
        SimpleNamespace(media_max_bytes=5 * 1024 * 1024),
    )
    assert result == "https://cdn.example/cat.jpg"


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
