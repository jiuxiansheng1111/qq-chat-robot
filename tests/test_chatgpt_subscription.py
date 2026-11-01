from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.chatgpt_subscription import (
    API_BASE,
    AUTH_ORIGIN,
    PLAN_SCOPE,
    ChatGPTSubscription,
    CredentialStore,
    LoginAttempt,
    SubscriptionError,
)


def _seed(store, *, token="synthetic-access", expired=False):
    state = {
        "host_id": "urn:uuid:synthetic-host", "active_client_id": "issued-client",
        "profiles": {"issued-client": {
            "client_id": "issued-client", "subject": "test-user", "email": "test@example.invalid",
            "scopes": [PLAN_SCOPE], "access_token": token, "refresh_token": "synthetic-refresh",
            "expires_at": time.time() + (-1 if expired else 3600),
        }},
    }
    store.write(state)
    return state


def _sse(*events, trailing_newline=True):
    data = "\n\n".join("event: ignored\ndata: " + json.dumps(event) for event in events)
    return data + ("\n\n" if trailing_newline else "")


def test_store_round_trip_and_encryption(tmp_path):
    store = CredentialStore(tmp_path)
    state = _seed(store)
    assert store.read() == state
    if os.name == "nt":
        assert b"synthetic-access" not in store.path.read_bytes()
        assert b"synthetic-refresh" not in store.path.read_bytes()
    else:
        assert store.path.stat().st_mode & 0o777 == 0o600


async def test_stable_host_new_registration_and_returning_login(tmp_path):
    subscription = ChatGPTSubscription(tmp_path)
    try:
        first = await subscription.prepare_login("http://127.0.0.1:1455/auth/callback")
        second = await subscription.prepare_login("http://127.0.0.1:1456/auth/callback")
        assert first.host_id == second.host_id
        query = parse_qs(urlparse(first.authorization_url()).query)
        assert query["client_id"] == ["dynamic_agent_client"]
        assert query["agent_name_hint"] == ["QQChatRobot"]
        assert query["code_challenge_method"] == ["S256"]
        assert first.verifier not in first.authorization_url()
        state = _seed(subscription.store)
        state["profiles"]["issued-client"]["id_token"] = "synthetic-id-hint"
        subscription.store.write(state)
        returning = await subscription.prepare_login("http://127.0.0.1:1455/auth/callback")
        query = parse_qs(urlparse(returning.authorization_url()).query)
        assert query["client_id"] == ["issued-client"]
        assert "agent_name_hint" not in query
        assert query["id_token_hint"] == ["synthetic-id-hint"]
        assert returning.subject == "test-user"
    finally:
        await subscription.aclose()


@pytest.mark.parametrize("change", ["state", "client", "expired", "denied"])
def test_invalid_callbacks_never_exchange_codes(change):
    attempt = LoginAttempt("issued-client", "host", "http://127.0.0.1:1455/auth/callback")
    params = {"state": attempt.state, "client_id": "issued-client", "code": "synthetic-code"}
    if change == "state":
        params["state"] = "different-state"
    elif change == "client":
        params["client_id"] = "another-client"
    elif change == "expired":
        attempt.created_at -= 601
    else:
        params["error"] = "access_denied"
    with pytest.raises(SubscriptionError):
        attempt.consume_callback(params)


def test_registration_uses_issued_client_and_callback_is_single_use():
    attempt = LoginAttempt("dynamic_agent_client", "host", "http://127.0.0.1:1455/auth/callback")
    params = {"state": attempt.state, "client_id": "issued-client", "code": "synthetic-code"}
    assert attempt.consume_callback(params) == ("issued-client", "synthetic-code")
    with pytest.raises(SubscriptionError):
        attempt.consume_callback(params)


@pytest.mark.parametrize("invalid", [None, "signature", "audience", "nonce", "subject"])
async def test_login_verifies_signed_identity_and_preserves_active_on_failure(tmp_path, invalid):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk["kid"] = "dummy-api-key-002"
    claims = {
        "iss": AUTH_ORIGIN, "aud": "issued-client", "sub": "test-user",
        "email": "test@example.invalid", "iat": int(time.time()), "exp": int(time.time()) + 600,
    }
    seen = []
    attempt = LoginAttempt(
        "issued-client", "host", "http://127.0.0.1:1455/auth/callback", subject="test-user",
    )
    claims["nonce"] = attempt.nonce
    if invalid == "audience":
        claims["aud"] = "other-client"
    if invalid == "nonce":
        claims["nonce"] = "other-nonce"
    if invalid == "subject":
        claims["sub"] = "other-user"
    signing = private if invalid != "signature" else rsa.generate_private_key(
        public_exponent=65537, key_size=2048,
    )
    token = jwt.encode(claims, signing, algorithm="RS256", headers={"kid": "dummy-api-key-002"})

    def receive(request):
        if request.url.path.endswith("/oauth/token"):
            seen.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json={
                "token_type": "Bearer", "access_token": "new-access", "refresh_token": "new-refresh",
                "id_token": token, "expires_in": 3600, "scope": PLAN_SCOPE,
            })
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={
                "issuer": AUTH_ORIGIN, "jwks_uri": f"{AUTH_ORIGIN}/jwks",
            })
        return httpx.Response(200, json={"keys": [jwk]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
    subscription = ChatGPTSubscription(tmp_path, client=client)
    old = _seed(subscription.store)
    try:
        callback = {"state": attempt.state, "code": "test-code", "client_id": "issued-client"}
        if invalid:
            with pytest.raises(SubscriptionError):
                await subscription.finish_login(attempt, callback)
            assert subscription.store.read()["profiles"] == old["profiles"]
        else:
            result = await subscription.finish_login(attempt, callback)
            assert result["connected"] and result["plan_usage_enabled"]
            assert subscription.store.read()["profiles"]["issued-client"]["access_token"] == "new-access"
        assert seen[0]["client_id"] == ["issued-client"]
        assert seen[0]["resource"] == [API_BASE]
        assert seen[0]["redirect_uri"] == [attempt.redirect_uri]
    finally:
        await subscription.aclose()


async def test_missing_direct_permission_blocks_all_inference_without_network(tmp_path):
    def receive(request):
        pytest.fail("No API request is permitted without the plan scope")

    client = httpx.AsyncClient(transport=httpx.MockTransport(receive))
    subscription = ChatGPTSubscription(tmp_path, client=client)
    state = _seed(subscription.store)
    state["profiles"]["issued-client"]["scopes"] = ["openid"]
    subscription.store.write(state)
    try:
        with pytest.raises(SubscriptionError, match="允许"):
            await subscription.list_models()
    finally:
        await subscription.aclose()


async def test_two_clients_serialize_rotating_refresh_and_use_new_credentials(tmp_path):
    calls = []

    async def receive(request):
        calls.append(parse_qs(request.content.decode()))
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={
            "token_type": "Bearer", "access_token": "rotated-access",
            "refresh_token": "rotated-refresh", "expires_in": 3600,
        })

    first = ChatGPTSubscription(tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(receive)))
    second = ChatGPTSubscription(tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(receive)))
    _seed(first.store, expired=True)
    try:
        assert await asyncio.gather(first._access_token(), second._access_token()) == [
            "rotated-access", "rotated-access",
        ]
        assert len(calls) == 1
        assert calls[0]["refresh_token"] == ["synthetic-refresh"]
        assert "scope" not in calls[0]
        assert first.store.read()["profiles"]["issued-client"]["refresh_token"] == "rotated-refresh"
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.parametrize("ending", ["complete", "slim_complete", "failed", "incomplete", "cut", "done"])
async def test_sse_accepts_only_completed_inference_and_rejects_late_quota_errors(tmp_path, ending):
    final = {"id": "response_1", "status": "completed", "output": [{
        "type": "message", "content": [{"type": "output_text", "text": "完整回答"}],
    }]}
    events = [{"type": "response.output_text.delta", "delta": "不应提前返回的文字"}]
    events.append({"type": "response.output_item.done", "output_index": 0, "item": final["output"][0]})
    if ending == "complete":
        events.append({"type": "response.completed", "response": final})
    elif ending == "slim_complete":
        events.append({"type": "response.completed", "response": {**final, "output": []}})
    elif ending in {"failed", "incomplete"}:
        events.append({"type": f"response.{ending}", "response": {"error": {
            "code": "subscription_sharing_usage_limit_exceeded", "message": "synthetic-secret",
        }}})
    content = _sse(*events, trailing_newline=False)
    if ending == "done":
        content += "\n\ndata: [DONE]\n\n"
    seen = []

    def receive(request):
        seen.append(request)
        return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})

    subscription = ChatGPTSubscription(
        tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(receive)),
    )
    _seed(subscription.store)
    payload = {"model": "gpt-6-luna", "input": [{"role": "user", "content": "测试"}],
               "store": False, "stream": True}
    try:
        if ending in {"complete", "slim_complete"}:
            assert await subscription.respond(payload) == final
        else:
            with pytest.raises(SubscriptionError) as failure:
                await subscription.respond(payload)
            assert "synthetic-secret" not in str(failure.value)
        assert len(seen) == 1
        assert str(seen[0].url) == f"{API_BASE}/responses"
        assert seen[0].headers["authorization"] == "Bearer synthetic-access"
    finally:
        await subscription.aclose()


async def test_admission_failure_keeps_http_code_and_request_id_without_secret_body(tmp_path):
    def receive(request):
        return httpx.Response(403, json={"detail": "synthetic-secret"}, headers={"x-request-id": "req-1"})

    subscription = ChatGPTSubscription(
        tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(receive)),
    )
    _seed(subscription.store)
    try:
        with pytest.raises(SubscriptionError) as failure:
            await subscription.list_models()
        assert "HTTP 403" in str(failure.value) and "req-1" in str(failure.value)
        assert "synthetic-secret" not in str(failure.value)
    finally:
        await subscription.aclose()


def test_payload_preserves_tools_and_context_without_forbidden_api_options():
    from app.astrbot_chatgpt_provider import subscription_payload

    source = {
        "model": "gpt-6-luna", "instructions": "角色不变",
        "input": [{"type": "message", "role": "system", "content": "规则"},
                  {"type": "function_call", "call_id": "c1", "name": "draw", "arguments": "{}"},
                  {"type": "function_call_output", "call_id": "c1", "output": "原生图 API 的结果"}],
        "temperature": 0.7, "previous_response_id": "old", "max_output_tokens": 3000,
    }
    before = copy.deepcopy(source)
    body = subscription_payload(source, {"max_tokens": 4096, "reasoning": {"effort": "low"}}, [{
        "type": "function", "function": {"name": "draw", "parameters": {"type": "object"}},
    }])
    assert source == before
    assert set(body) == {"model", "input", "instructions", "reasoning", "tools", "store", "stream"}
    assert body["input"][0]["role"] == "developer"
    assert body["input"][1]["namespace"] == "qqchat"
    assert body["input"][2] == source["input"][2]
    assert body["tools"][0]["tools"][0]["name"] == "draw"
    assert not body["store"] and body["stream"]


def test_config_changes_only_chat_selection_and_adds_a_separate_provider(tmp_path):
    from app.chatgpt_subscription_config import MODEL_ID, subscription_config

    original = {
        "provider_sources": [{"id": "zhipu", "key": ["synthetic-existing-key"]}],
        "provider": [{"id": "vision", "model": "existing-vision"}],
        "agent_runner": {"runner_type": "local", "config": {
            "model": {"provider_id": "old", "fallback_provider_ids": ["old-fallback"]},
            "persona": {"persona_id": "小丛雨"}, "tools": {"draw": "existing-provider"},
        }},
        "provider_tts_settings": {"provider_id": "existing-tts"},
        "image_generation": {"model": "existing-image", "api_key": "dummy-api-key-001"},
    }
    before = copy.deepcopy(original)
    changed = subscription_config(original, tmp_path)
    assert original == before
    assert changed["provider_sources"][0] == original["provider_sources"][0]
    assert changed["provider"][0] == original["provider"][0]
    assert changed["agent_runner"]["config"]["model"]["provider_id"] == MODEL_ID
    assert changed["agent_runner"]["config"]["persona"] == original["agent_runner"]["config"]["persona"]
    assert changed["image_generation"] == original["image_generation"]
    assert changed["provider_tts_settings"] == original["provider_tts_settings"]
    assert subscription_config(changed, tmp_path) == changed


async def test_verify_never_substitutes_another_model():
    from app.chatgpt_subscription_config import verify_subscription

    class MissingLuna:
        async def list_models(self):
            return [{"slug": "gpt-other"}]

        async def respond(self, payload):
            pytest.fail("A missing requested model must not silently use a different one")

    with pytest.raises(SubscriptionError, match="gpt-6-luna"):
        await verify_subscription(MissingLuna())
