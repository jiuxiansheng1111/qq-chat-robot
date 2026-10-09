import httpx
import pytest

from app.llm.providers import LLMError, OpenAICompatibleProvider, llm_failure_reply


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


@pytest.mark.parametrize("error, expected", [
    (LLMError("AstrBot 模型调用失败：APIConnectionError"), "连不上聊天模型"),
    (LLMError("AstrBot 模型调用失败：APITimeoutError"), "回复超时"),
    (LLMError("llm_queue_full"), "请求有点多"),
])
def test_failure_reply_distinguishes_connectivity_from_busy_queue(error, expected):
    assert expected in llm_failure_reply(error)


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
