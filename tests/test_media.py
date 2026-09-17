from types import SimpleNamespace

import httpx
import pytest

from app.plugins.media import random_image


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
