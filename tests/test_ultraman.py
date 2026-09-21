import base64
from types import SimpleNamespace

import httpx
import pytest

import app.services.ultraman as ultraman_module
from app.services.ultraman import (
    ULTRAMAN_DEBUT_YEARS,
    ULTRAMAN_PROFILES,
    ULTRAMAN_ROSTER,
    official_ultraman_image,
    ultraman_profile_text,
)


def test_every_ultraman_has_complete_collection_card_content():
    assert len(ULTRAMAN_ROSTER) >= 30
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
