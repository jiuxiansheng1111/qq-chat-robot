import asyncio
import json
import re
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services import game_clients
from app.services.account_hub import AccountHub, _minecraft_cache
from app.services.mahjong.account_panel import account_app


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(game_clients, "CONFIG", tmp_path / "data/game-companion.json")
    service = SimpleNamespace(status=lambda: {"configured": False, "account_hint": "", "error": ""})
    hub = AccountHub(tmp_path, service)
    monkeypatch.setattr(hub, "netease_status", AsyncMock(return_value={"state": "online", "url": "http://127.0.0.1:3010/login"}))
    return hub


def test_account_page_controls_share_robot_config_without_exposing_credentials(hub, monkeypatch):
    base = "http://127.0.0.1:6190"
    app = account_app(game_clients.ROOT, hub.mahjong, hub=hub)
    monkeypatch.setattr(game_clients, "steam_client", lambda account: (None, "wrong_account" if account else "no_account"))
    monkeypatch.setattr(game_clients, "open_steam", lambda: True)
    # A successful bind/unbind must retain unrelated game settings.
    game_clients.CONFIG.parent.mkdir(parents=True)
    game_clients.CONFIG.write_text(json.dumps({"steam": {"web_api_key": "test-not-for-display"}}))
    with TestClient(app, base_url=base) as client:
        page = client.get("/accounts")
        nonce = re.search(r'<script nonce="([^"]+)">', page.text)[1]
        headers = {"Origin": base, "X-QQChat-CSRF": nonce}
        def action(route, body=None):
            return client.post("/api/accounts/" + route, json=body or {}, headers=headers)

        assert page.status_code == 200 and page.headers["Cache-Control"] == "no-store"
        assert '/accounts' in client.get("/").text
        assert client.post("/api/accounts/steam/unbind", json={}).status_code == 403
        assert action("minecraft/server", {"server": "localhost:70000"}).status_code == 400
        assert action("minecraft/server", {"server": "localhost:25575"}).json() == {"ok": True}
        assert action("steam/bind", {"account": "123"}).status_code == 400
        steam_id = str(game_clients.STEAM_BASE + 71)
        assert action("steam/bind", {"account": steam_id}).json() == {"ok": True}
        assert action("steam/open").json() == {"ok": True}
        snapshot = client.get("/api/accounts")
        assert snapshot.json()["steam"] == {"state": "wrong_account", "hint": steam_id[-4:]}
        assert snapshot.json()["minecraft"] == {"state": "signed_out", "server": "localhost:25575"}
        assert set(snapshot.json()) == {"chatgpt", "netease", "mahjong", "minecraft", "steam", "bilibili"}
        assert "test-not-for-display" not in snapshot.text and steam_id not in snapshot.text
        assert action("steam/unbind").is_success
        assert game_clients.settings()["steam"]["web_api_key"] == "test-not-for-display"
        assert action("unknown/login").status_code == 404
        assert action("steam/bind", ["wrong shape"]).status_code == 400


@pytest.mark.asyncio
async def test_device_login_cancels_or_finishes_without_joining_a_server(hub):
    if not shutil.which("node"):
        pytest.skip("Node is required for device login")
    mc = hub.minecraft
    helper = hub.root / "integrations/minecraft-player/account.cjs"
    (helper.parent / "node_modules/prismarine-auth").mkdir(parents=True)
    mc.folder.mkdir(parents=True)
    helper.write_text(
        "const fs = require('node:fs'), path = require('node:path');\n"
        "console.log(JSON.stringify({state:'device', code:'TESTCODE'}));\n"
        "const timer = setInterval(() => {if (fs.existsSync(path.join(process.argv[3], 'finish'))) {\n"
        "clearInterval(timer); console.log(JSON.stringify({state:'verified'}));}}, 30);\n"
    )
    try:
        assert (await mc.start())["state"] == "starting"
        first_job = mc.task
        await mc.start()
        assert mc.task is first_job
        async with asyncio.timeout(5):
            while mc.state != "device":
                await asyncio.sleep(.02)
        assert mc.progress()["code"] == "TESTCODE" and hub.minecraft_status()["state"] == "pending"
        assert (await mc.player.command(("test",), "join"))["status"] == "login_in_progress"
        assert "TESTCODE" not in json.dumps(await hub.statuses())
        await mc.cancel()
        assert mc.progress() == {"state": "idle"} and not mc.player.authorizing
        (mc.folder / "finish").touch()
        await mc.start()
        await asyncio.wait_for(mc.task, 5)
        assert mc.progress() == {"state": "verified"} and not mc.player.authorizing
        cache = _minecraft_cache(mc.folder, "Dafeiyu")
        cache.write_text('{"mca":{"access_token":"test-token"}}')
        other = _minecraft_cache(mc.folder, "Other")
        other.write_text('{"mca":{"access_token":"other-test-token"}}')
        assert hub.minecraft_status()["state"] == "saved"
        assert "test-token" not in json.dumps(await hub.statuses())
        await mc.forget()
        assert not cache.exists() and other.exists() and hub.minecraft_status()["state"] == "signed_out"
    finally:
        await hub.close()


@pytest.mark.asyncio
async def test_chat_login_starts_a_real_local_callback_only_on_demand(hub):
    login = hub.chat_login
    # Bind an ephemeral port for this test, leaving the registered callback port for the user.
    login.listener.port = 0
    try:
        assert login.listener.server is None
        assert (await hub.update("chatgpt", "login", {}))["url"] == "http://127.0.0.1:1455/"
        worker = login.listener.task
        await hub.update("chatgpt", "login", {})
        assert login.listener.task is worker and hub.chat_status()["state"] == "pending"
        port = login.listener.socket.getsockname()[1]
        async with httpx.AsyncClient(trust_env=False, base_url=f"http://127.0.0.1:{port}") as client:
            page = await client.get("/")
            assert page.is_success and "Continue with ChatGPT" in page.text
        await hub.update("chatgpt", "cancel", {})
        assert login.listener.socket is None and hub.chat_status()["state"] == "signed_out"
    finally:
        await hub.close()
