from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx

from app.chatgpt_subscription import ChatGPTSubscription
from scripts.connect_chatgpt import login_app


async def test_local_login_page_and_callback_do_not_log_or_accept_unverified_code(tmp_path):
    def upstream(request):
        raise AssertionError("Unverified callbacks must not exchange a code")

    subscription = ChatGPTSubscription(
        tmp_path, client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    args = SimpleNamespace(
        store=tmp_path, model="gpt-6-luna", port=1455, activate=True,
        new_account=False, profile="", open_browser=False,
    )
    app = login_app(subscription, args)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:1455")
    try:
        page = await client.get("/")
        assert page.status_code == 200
        assert "Continue with ChatGPT" in page.text
        assert page.headers["referrer-policy"] == "no-referrer"
        start = await client.get("/login")
        assert start.status_code == 303
        url = urlparse(start.headers["location"])
        assert url.netloc == "auth.openai.com"
        params = parse_qs(url.query)
        assert params["client_id"] == ["dynamic_agent_client"]
        bad = await client.get("/auth/callback?state=bad&code=synthetic-code&client_id=issued-client")
        assert bad.status_code == 400
        assert "synthetic-code" not in bad.text
        status = (await client.get("/status")).json()
        assert status["status"] == "failed" and not status["activated"]
        assert not status["connected"]
    finally:
        await client.aclose()
        await subscription.aclose()


async def test_login_rejects_dns_rebinding_host(tmp_path):
    subscription = ChatGPTSubscription(tmp_path)
    args = SimpleNamespace(store=tmp_path, model="gpt-6-luna", port=1455)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(login_app(subscription, args)),
        base_url="http://127.0.0.1:1455",
    )
    try:
        response = await client.get("/", headers={"host": "attacker.invalid"})
        assert response.status_code == 403
        assert not (tmp_path / "credentials.bin").exists()
    finally:
        await client.aclose()
        await subscription.aclose()
