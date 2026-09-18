from types import SimpleNamespace

import httpx
import pytest

import app.plugins.media as media_module
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


@pytest.mark.asyncio
async def test_pollinations_uses_a_new_seed_for_each_image(monkeypatch):
    seen_seeds = []

    async def handler(request):
        seen_seeds.append(request.url.params["seed"])
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"image")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    seeds = iter((123, 456))
    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    monkeypatch.setattr(media_module.secrets, "randbelow", lambda _: next(seeds))
    settings = SimpleNamespace(media_max_bytes=5 * 1024 * 1024)
    url = "https://gen.pollinations.ai/image/a%20cute%20pig?model=black-forest-labs/flux.1-schnell&seed=0"

    await random_image(url, "pig-key", settings)
    await random_image(url, "pig-key", settings)

    assert seen_seeds == ["123", "456"]
