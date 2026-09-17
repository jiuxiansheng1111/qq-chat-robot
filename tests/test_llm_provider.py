import httpx
import pytest

from app.llm.providers import LLMError, OpenAICompatibleProvider


async def test_provider_reuses_http_client():
    provider = OpenAICompatibleProvider(
        "test", "https://example.invalid", "key", "model", 1, 10, 0.1
    )
    try:
        assert isinstance(provider.client, httpx.AsyncClient)
        assert provider.client.is_closed is False
    finally:
        await provider.aclose()
    assert provider.client.is_closed is True


async def test_provider_rejects_empty_content():
    async def handler(_request):
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})

    provider = OpenAICompatibleProvider(
        "test", "https://example.invalid", "key", "model", 1, 10, 0.1
    )
    await provider.aclose()
    provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(LLMError, match="empty response"):
            await provider.chat([{"role": "user", "content": "hello"}])
    finally:
        await provider.aclose()
