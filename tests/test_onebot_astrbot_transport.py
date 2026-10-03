import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.services import onebot_routing
from app.services.onebot_routing import (
    ASTRBOT_API_BASE,
    current_onebot_self_id,
    onebot_client,
    onebot_route,
    register_action_client,
    set_current_onebot_self_id,
    track_replies,
    unregister_action_client,
)


def _settings():
    return SimpleNamespace(
        onebot_self_id="configured-primary",
        onebot_api_base="http://127.0.0.1:3000",
        onebot_access_token="primary-access",
        onebot_webhook_token=" raw-primary-hook ",
        onebot_self_id_2="configured-secondary",
        onebot_api_base_2="http://127.0.0.1:3001",
        onebot_access_token_2="secondary-access",
        onebot_webhook_token_2="raw-secondary-hook",
    )


@pytest.fixture(autouse=True)
def reset_routing_context():
    set_current_onebot_self_id("")
    yield
    set_current_onebot_self_id("")
    for self_id in ("runtime-a", "runtime-b", "runtime-c", "test-bot"):
        unregister_action_client(self_id)


@pytest.mark.asyncio
async def test_mock_transport_preserves_text_image_and_record_payloads():
    calls = []
    segments = [
        {"type": "text", "data": {"text": "你好"}},
        {"type": "image", "data": {"file": "https://example.invalid/image.png"}},
        {"type": "record", "data": {"file": "file:///tmp/voice.wav", "magic": 0}},
    ]

    async def action_call(action, **params):
        calls.append((action, params))
        return {"message_id": 123}

    register_action_client("test-bot", action_call)
    settings = _settings()
    set_current_onebot_self_id("test-bot")
    route = onebot_route(settings)
    assert route.self_id == "test-bot"
    assert route.api_base == ASTRBOT_API_BASE
    assert route.access_token == ""
    assert route.webhook_token == " raw-primary-hook "

    async with onebot_client(settings, timeout=2, trust_env=False) as client:
        response = await client.post(
            f"{route.api_base}/send_group_msg",
            json={"group_id": "42", "message": segments},
        )
    assert response.json() == {
        "status": "ok", "retcode": 0, "data": {"message_id": 123},
    }
    assert calls == [("send_group_msg", {"group_id": "42", "message": segments})]


@pytest.mark.asyncio
async def test_registered_clients_isolate_bots_and_keep_actual_incoming_self_id():
    async def bot_a(action, **params):
        return {"bot": "a", "action": action, "params": params}

    async def bot_b(action, **params):
        return {"bot": "b", "action": action, "params": params}

    register_action_client("runtime-a", bot_a)
    register_action_client("runtime-b", bot_b)
    settings = _settings()
    settings.onebot_self_id_2 = "runtime-b"
    set_current_onebot_self_id("runtime-a")

    route_a = onebot_route(settings)
    assert route_a.self_id == "runtime-a"
    assert route_a.webhook_token == " raw-primary-hook "
    explicit_b = onebot_route(settings, "runtime-b")
    assert explicit_b.self_id == "runtime-b"
    assert explicit_b.webhook_token == "raw-secondary-hook"
    assert current_onebot_self_id() == "runtime-a"

    async with onebot_client(settings) as client_a:
        response_a = await client_a.post(
            f"{route_a.api_base}/send_msg", json={"message": "from a"}
        )
    async with onebot_client(settings, self_id="runtime-b") as client_b:
        response_b = await client_b.post(
            f"{explicit_b.api_base}/send_private_msg", json={"user_id": "9", "message": "from b"}
        )
    assert response_a.json()["data"]["bot"] == "a"
    assert response_b.json()["data"]["bot"] == "b"


def test_unregistered_bot_keeps_httpx_client_constructor_behavior(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(onebot_routing.httpx, "AsyncClient", FakeAsyncClient)
    settings = _settings()

    client = onebot_client(settings, timeout=7, trust_env=False)

    assert isinstance(client, FakeAsyncClient)
    assert calls == [{"timeout": 7, "trust_env": False}]
    assert onebot_route(settings).api_base == "http://127.0.0.1:3000"


@pytest.mark.asyncio
async def test_callback_exceptions_and_timeouts_become_httpx_errors():
    settings = _settings()

    async def broken(action, **params):
        raise RuntimeError("private callback detail")

    register_action_client("test-bot", broken)
    set_current_onebot_self_id("test-bot")
    async with onebot_client(settings, timeout=1) as client:
        with pytest.raises(httpx.HTTPError) as error:
            await client.post(f"{ASTRBOT_API_BASE}/get_group_info", json={"group_id": "1"})
    assert "private callback detail" not in str(error.value)

    async def slow(action, **params):
        await asyncio.sleep(0.1)
        return {"ok": True}

    register_action_client("test-bot", slow)
    async with onebot_client(settings, timeout=0.01) as client:
        with pytest.raises(httpx.HTTPError):
            await client.post(f"{ASTRBOT_API_BASE}/get_group_info", json={"group_id": "1"})


@pytest.mark.asyncio
async def test_reply_tracking_counts_attempts_and_successful_payloads():
    settings = _settings()

    async def action_call(action, **params):
        if action == "send_private_msg":
            return {"status": "failed", "retcode": 100, "data": {"reason": "rejected"}}
        if action == "send_group_forward_msg":
            return {"status": "ok", "retcode": 0, "data": {"message_id": 2}}
        return []

    register_action_client("test-bot", action_call)
    set_current_onebot_self_id("test-bot")
    with track_replies() as counts:
        async with onebot_client(settings) as client:
            failed = await client.post(
                f"{ASTRBOT_API_BASE}/send_private_msg", json={"user_id": "2", "message": "x"}
            )
            success = await client.post(
                f"{ASTRBOT_API_BASE}/send_group_forward_msg", json={"group_id": "2", "messages": []}
            )
            await client.post(f"{ASTRBOT_API_BASE}/get_group_info", json={"group_id": "2"})
        assert failed.json()["status"] == "failed"
        assert success.json()["data"]["message_id"] == 2
        assert (counts.attempted, counts.succeeded) == (2, 1)


@pytest.mark.asyncio
async def test_reply_tracking_contexts_are_isolated_across_concurrent_bots():
    settings = _settings()

    async def action_call(action, **params):
        await asyncio.sleep(0)
        return {"sent": True}

    register_action_client("runtime-a", action_call)
    register_action_client("runtime-b", action_call)

    async def send_for(self_id):
        set_current_onebot_self_id(self_id)
        with track_replies() as counts:
            async with onebot_client(settings) as client:
                await client.post(f"{ASTRBOT_API_BASE}/send_group_msg", json={"message": self_id})
            return counts.attempted, counts.succeeded

    result_a, result_b = await asyncio.gather(send_for("runtime-a"), send_for("runtime-b"))
    assert result_a == (1, 1)
    assert result_b == (1, 1)


@pytest.mark.asyncio
async def test_unregister_and_close_release_transport():
    settings = _settings()

    async def action_call(action, **params):
        return {"ok": True}

    register_action_client("test-bot", action_call)
    set_current_onebot_self_id("test-bot")
    client = onebot_client(settings)
    assert not client.is_closed
    await client.aclose()
    assert client.is_closed
    assert unregister_action_client("test-bot") is True
    assert unregister_action_client("test-bot") is False
    assert onebot_route(settings).api_base == "http://127.0.0.1:3000"
