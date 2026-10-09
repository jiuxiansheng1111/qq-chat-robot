import asyncio
from types import SimpleNamespace

from app.astrbot_llm import AstrBotChatProvider, model_session, route_provider_http_client
from app.config import Settings
from app.services.http_routing import ProxyFallbackTransport


class FakeSDK:
    def __init__(self, http_client=None):
        self.http_client = http_client
        self.closed = 0
        self.api_key = "synthetic-key"
        self.base_url = "https://model.invalid/v1"

    def with_options(self, *, http_client):
        replacement = FakeSDK(http_client)
        assert replacement.api_key == self.api_key
        assert replacement.base_url == self.base_url
        return replacement

    async def close(self):
        self.closed += 1
        if self.http_client is not None:
            await self.http_client.aclose()


async def test_concurrent_model_binding_replaces_sdk_client_only_once():
    original = FakeSDK()
    provider = SimpleNamespace(client=original, timeout=12, provider_config={"proxy": ""})
    settings = Settings(_env_file=None)
    await asyncio.gather(*(route_provider_http_client(provider, settings) for _ in range(10)))
    try:
        assert original.closed == 1
        assert provider.client is provider._qqchat_routed_client
        assert isinstance(provider.client.http_client._transport, ProxyFallbackTransport)
        assert provider.client.http_client.timeout.read == 12
    finally:
        await provider.client.close()


async def test_chat_with_session_keeps_astrbot_primary_fallback_and_contexts():
    called = []
    original = FakeSDK()

    async def primary_chat(**kwargs):
        called.append(("primary", kwargs))
        raise RuntimeError("synthetic primary failure")

    async def fallback_chat(**kwargs):
        called.append(("fallback", kwargs))
        return SimpleNamespace(completion_text="ok")

    providers = {
        "primary": SimpleNamespace(text_chat=primary_chat),
        "fallback": SimpleNamespace(
            text_chat=fallback_chat, client=original, timeout=10, provider_config={},
        ),
    }

    async def selected_model(umo):
        assert umo == "test:group:42"
        return "primary"

    context = SimpleNamespace(
        get_config=lambda *args: {"agent_runner": {"runner_type": "local", "config": {
            "model": {"provider_id": "primary", "fallback_provider_ids": ["fallback"]},
        }}},
        get_current_chat_provider_id=selected_model,
        get_provider_by_id=providers.get,
    )
    messages = [{"role": "user", "content": "hello"}]
    try:
        with model_session("test:group:42"):
            assert await AstrBotChatProvider(context, Settings(_env_file=None)).chat(messages) == "ok"
        assert [name for name, _ in called] == ["primary", "fallback"]
        assert all(kwargs["contexts"] == messages for _, kwargs in called)
        assert original.closed == 1
    finally:
        await providers["fallback"].client.close()


async def test_panel_reloaded_provider_client_gets_routing_again():
    provider = SimpleNamespace(client=FakeSDK(), timeout=10, provider_config={})
    settings = Settings(_env_file=None)
    await route_provider_http_client(provider, settings)
    await provider.client.close()
    reloaded = FakeSDK()
    provider.client = reloaded
    await route_provider_http_client(provider, settings)
    try:
        assert reloaded.closed == 1
        assert isinstance(provider.client.http_client._transport, ProxyFallbackTransport)
    finally:
        await provider.client.close()
