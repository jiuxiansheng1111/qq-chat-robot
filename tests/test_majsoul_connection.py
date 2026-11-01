"""Official wire contracts, credential boundaries, complete-game conversion and native routing."""

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.astrbot_mahjong import request_args
from app.services.mahjong.account_panel import account_app
from app.services.mahjong.liqi import decode, encode, record_actions
from app.services.mahjong.majsoul_client import (
    ClientVersion,
    MajsoulError,
    MajsoulRPC,
    MajsoulService,
    current_client_version,
    link_identity,
    official_session,
)
from app.services.mahjong.majsoul_records import convert_game, whole_game_overview
from app.services.mahjong.records import RecordError, decision_input
from app.services.mahjong.review import review

ROOT = Path(__file__).resolve().parents[1]
LINK = "https://game.maj-soul.com/1/?paipu=261023-11111111-2222-3333-4444-555555555555_42"
HAND = ["1m", "2m", "3m", "4m", "5m", "6m", "7p", "8p", "9p", "2s", "3s", "4s", "1z"]


def game():
    head = {"uuid": link_identity(LINK)[0], "end_time": 100, "config": {"category": 2, "mode": {"mode": 1}},
            "accounts": [{"account_id": 42, "seat": 0}],
            "result": {"players": [{"seat": seat} for seat in range(4)]}}
    records = []
    for dealer, scores, end in [(0, [25000] * 4, [17000, 33000, 25000, 25000]),
                                (1, [17000, 33000, 25000, 25000], [17000, 25000, 33000, 25000])]:
        start = {"chang": 0, "ju": dealer, "ben": 0, "liqibang": 0, "doras": ["5z"], "scores": scores}
        start.update({"tiles" + str(i): HAND + (["7s"] if i == dealer else []) for i in range(4)})
        records += [
            {"name": "RecordNewRound", "data": start},
            {"name": "RecordDiscardTile", "data": {"seat": dealer, "tile": "7s", "moqie": True}},
            {"name": "RecordHule", "data": {"hules": [{"seat": dealer + 1, "zimo": False}], "scores": end}},
        ]
    return head, records


def binary(records, modern=True):
    wrapped = [encode("Wrapper", {"name": ".lq." + r["name"], "data": encode(r["name"], r["data"])}) for r in records]
    data = {"version": 210715, "actions": [{"type": 1, "result": r} for r in wrapped]} if modern else {"records": wrapped}
    return encode("Wrapper", {"name": ".lq.GameDetailRecords", "data": encode("GameDetailRecords", data)})


@pytest.mark.parametrize("modern", [True, False])
def test_binary_full_match_and_visible_boundary(modern):
    head, records = game()
    decoded = record_actions(binary(records, modern))
    replay, seat = convert_game(head, decoded, 42)
    assert seat == 0 and len(replay.rounds) == 2 and replay.source == "majsoul-official-rpc"
    assert replay.rounds[0]["events"][0] == {"type": "draw", "actor": 0, "tile": "7s"}
    assert replay.rounds[0]["result"]["scores"] == [17000, 33000, 25000, 25000]
    visible = decision_input(replay, seat)
    assert all("hands" not in r and "result" not in r for r in visible.rounds)
    assert "整场共2局" in whole_game_overview(replay, seat)[0]
    assert len(review(replay, seat).analysis.decisions) == 1


def test_wire_packed_signed_scores_and_default_zero_seat():
    # Field 5, packed int32: 25000, -1000 (protobuf sign-extended 64-bit varint).
    raw = bytes.fromhex("2a0da8c30198f8ffffffffffffff01")
    assert decode("RecordNewRound", raw)["scores"] == [25000, -1000]
    assert decode("RecordDiscardTile", b"\x12\x021m")["seat"] == 0


@pytest.mark.parametrize("mutation", ["truncated", "unknown", "score_gap", "three_player", "missing_start"])
def test_incomplete_and_unsupported_matches_never_become_full_analysis(mutation):
    head, records = game()
    if mutation == "truncated":
        records.pop()
    elif mutation == "unknown":
        records.insert(1, {"name": "RecordUnimplemented", "data": {}})
    elif mutation == "score_gap":
        records[3]["data"]["scores"][0] += 1000
    elif mutation == "three_player":
        head["result"]["players"].pop()
    else:
        records = records[3:]
    with pytest.raises(RecordError):
        convert_game(head, records, 42)


def test_riichi_acceptance_and_red_ankan_are_preserved():
    head, _ = game()
    start = {"chang": 0, "ju": 0, "ben": 0, "liqibang": 0, "doras": ["5z"], "scores": [25000] * 4,
             **{"tiles" + str(i): HAND.copy() for i in range(4)}}
    start["tiles0"] = ["0m", "5m", "5m", "5m", *HAND[:9], "9s"]
    records = [{"name": "RecordNewRound", "data": start},
               {"name": "RecordAnGangAddGang", "data": {"seat": 0, "type": 3, "tiles": "5m", "doras": ["5z", "6z"]}},
               {"name": "RecordDealTile", "data": {"seat": 0, "tile": "8s"}},
               {"name": "RecordDiscardTile", "data": {"seat": 0, "tile": "8s", "is_liqi": True}},
               {"name": "RecordDealTile", "data": {"seat": 1, "tile": "8p", "liqi": {"seat": 0, "score": 24000}}},
               {"name": "RecordLiuJu", "data": {"type": 3}}]
    # Only four fives: avoid duplicating the five in HAND[:9].
    start["tiles0"][8] = "8m"
    replay, _ = convert_game(head, records, 42)
    events = replay.rounds[0]["events"]
    assert events[1]["consumed"] == ["0m", "5m", "5m", "5m"]
    assert events[2]["type"] == "dora"
    assert next(e for e in events if e["type"] == "riichi")["accepted"] is False
    assert sum(e["type"] == "riichi_accept" for e in events) == 1
    assert replay.rounds[0]["result"]["scores"][0] == 24000


async def test_rpc_errors_are_credential_safe():
    class Socket:
        async def send(self, data):
            self.sent = data

        async def recv(self):
            data = encode("ResLogin", {"error": {"code": 1004, "str_params": ["secret-password"]}})
            return b"\x03\x01\x00" + encode("Wrapper", {"data": data})

    with pytest.raises(MajsoulError) as error:
        await MajsoulRPC(Socket()).call("login", "ReqLogin", "ResLogin", {"account": "test", "password": "secret-password"})
    assert error.value.code == 1004 and "secret-password" not in str(error.value)


@pytest.mark.parametrize("unity6", [True, False])
async def test_version_uses_current_asset_and_separate_package(unity6):
    requested = []

    def respond(request):
        requested.append(request.url.path)
        if request.url.path.endswith("index.html"):
            content = b'productVersion: "4.0.51"'
        elif request.url.path.endswith("bundle_info_so.majset"):
            content = b'\x00td1bd8bb002s_a8a5b47edee1b8ac51a4\x00'
        else:
            if "/u6v_" in request.url.path and not unity6:
                return httpx.Response(404)
            # Unity bundle includes binary prefixes, not a JSON document.
            content = b'\x00\xf3\x11":"0.16.289","platform":"WebGL"}@\x00'
        return httpx.Response(200, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        version = await current_client_version(client)
    assert version == ClientVersion(resource="0.16.289", package="4.0.51")
    assert version.wire == "WebGL_2022-0.16.289"
    assert not any("version.json" in path for path in requested)


async def test_unknown_resource_version_stops_before_credentials_are_sent():
    def respond(request):
        content = (b'productVersion: "4.0.51"' if request.url.path.endswith("index.html")
                   else b"unknown manifest layout")
        return httpx.Response(200, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(MajsoulError, match="资源版本"):
            await current_client_version(client)


async def test_login_sends_route_handshake_and_current_version(monkeypatch):
    calls = []

    class Socket:
        closed = False

        async def send(self, frame):
            self.serial = frame[1:3]
            wrapper = decode("Wrapper", frame[3:])
            self.method = wrapper["name"]
            request_type = "ReqRouteConnection" if self.method.endswith("requestConnection") else "ReqLogin"
            calls.append((self.method, decode(request_type, wrapper["data"])))

        async def recv(self):
            response_type = "ResCommon" if self.method.endswith("requestConnection") else "ResLogin"
            return b"\x03" + self.serial + encode("Wrapper", {"data": encode(response_type, {})})

        async def close(self):
            self.closed = True

    socket = Socket()

    async def connect(*args, **kwargs):
        return socket

    async def settings(client):
        return ClientVersion("0.16.289", "4.0.51"), ["wss://route-3.maj-soul.com:8443/gateway"]

    monkeypatch.setattr("app.services.mahjong.majsoul_client.server_settings", settings)
    monkeypatch.setattr("app.services.mahjong.majsoul_client.websockets.connect", connect)
    async with official_session("test-account", "test-hash") as (_, _, wire):
        assert wire == "WebGL_2022-0.16.289"
    assert socket.closed
    assert [name for name, _ in calls] == [".lq.Route.requestConnection", ".lq.Lobby.login"]
    route, login = calls[0][1], calls[1][1]
    assert route["kind"] == 1 and route["route_id"] == "route-3" and route["platform"] == "Web"
    assert login["client_version"] == {"resource": "0.16.289", "package": "4.0.51"}
    assert login["client_version_string"] == wire and not login["reconnect"]
    assert not login["gen_access_token"]


async def test_151_identifies_client_incompatibility_without_echoing_upstream():
    class Socket:
        async def send(self, data):
            pass

        async def recv(self):
            data = encode("ResLogin", {"error": {"code": 151, "json_param": "secret-password"}})
            return b"\x03\x01\x00" + encode("Wrapper", {"data": data})

    with pytest.raises(MajsoulError, match="客户端版本") as error:
        await MajsoulRPC(Socket()).call("login", "ReqLogin", "ResLogin", {})
    assert error.value.code == 151 and "secret-password" not in str(error.value)


async def test_save_validates_before_replacing_and_encrypts(monkeypatch, tmp_path):
    @asynccontextmanager
    async def accepted(account, hashed):
        assert hashed != "test-password" and len(hashed) == 64
        yield None

    monkeypatch.setattr("app.services.mahjong.majsoul_client.official_session", accepted)
    service = MajsoulService(tmp_path)
    await service.save("test-account", "test-password", False)
    original = service.store.path.read_bytes()
    assert b"test-password" not in original
    assert service.status()["configured"] and "password" not in json.dumps(service.status())

    @asynccontextmanager
    async def rejected(*args):
        raise MajsoulError("synthetic failure")
        yield

    monkeypatch.setattr("app.services.mahjong.majsoul_client.official_session", rejected)
    with pytest.raises(MajsoulError):
        await service.save("different", "wrong-password", True)
    assert service.store.path.read_bytes() == original
    await service.forget()
    assert not service.status()["configured"]


def test_account_form_has_csrf_host_and_secret_response_boundaries(tmp_path):
    class Service:
        configured = False

        def status(self):
            return {"configured": self.configured}

        async def save(self, account, password, auto_open):
            assert account == "test" and password == "synthetic-password"
            self.configured = True

    service = Service()
    with TestClient(account_app(ROOT, service), base_url="http://127.0.0.1:6190") as client:
        page = client.get("/")
        token = re.search(r"const token='([^']+)'", page.text)[1]
        data = {"account": "test", "password": "synthetic-password", "auto_open": True}
        assert client.post("/account", json=data).status_code == 403
        headers = {"origin": "http://127.0.0.1:6190", "x-qqchat-csrf": token}
        assert client.get("/status", headers={"host": "evil.test"}).status_code == 403
        response = client.post("/account", json=data, headers=headers)
        assert response.json()["ok"] and "synthetic-password" not in response.text
        assert response.headers["cache-control"] == "no-store"
        response = client.post("/account", json={**data, "auto_open": "invalid"}, headers=headers)
        assert response.status_code == 400 and "synthetic-password" not in response.text


@pytest.mark.parametrize("kind", ["private", "group"])
def test_exact_user_phrase_reaches_replay_handler(kind):
    text = "雀魂分析 雀魂牌谱:" + LINK
    event = SimpleNamespace(get_platform_name=lambda: "aiocqhttp", get_message_str=lambda: text,
                            get_extra=lambda *args: "", message_obj=SimpleNamespace(raw_message={
                                "message_type": kind, "self_id": "bot", "user_id": "human", "sender": {}}))
    assert request_args(event) == "链接=" + LINK
    event.get_message_str = lambda: "不要分析雀魂牌谱 " + LINK
    assert request_args(event) is None


async def test_plugin_link_runs_every_round_and_exports(monkeypatch, tmp_path):
    import app.plugins.mahjong as plugin

    head, records = game()

    class Service:
        async def fetch(self, link):
            assert link == LINK
            return head, records, 42

    monkeypatch.setattr("app.services.mahjong.majsoul_client.service_for", lambda root: Service())
    monkeypatch.setattr(plugin, "_root", lambda ctx: tmp_path)
    state = SimpleNamespace()
    context = SimpleNamespace(args="链接=" + LINK + " AI=关闭", group_id="g", user_id="u", event={"self_id": "bot"},
                              request=SimpleNamespace(app=SimpleNamespace(state=state)))
    result = await plugin.handle_mahjong(context)
    assert "整场共2局" in result and "东1局" in result and "东2局" in result
    job = next(iter(state.mahjong_reports.jobs))
    context.args = "导出 " + job
    assert "已保存" in await plugin.handle_mahjong(context)
    report = (tmp_path / f"data/mahjong/reports/{job}.md").read_text(encoding="utf-8")
    assert "整场走势" in report and "17000" in report


@pytest.mark.parametrize("kind,allowed", [("private", True), ("group", True), ("group", False)])
async def test_native_hook_handles_link_before_llm_and_respects_group_acl(monkeypatch, tmp_path, kind, allowed):
    from test_astrbot_plugin import FakeEvent, _load_plugin

    from app.plugins import mahjong

    class Database:
        async def group_enabled(self, group):
            return allowed

        async def is_blocked(self, group, user):
            return False

        async def plugin_enabled(self, group, plugin):
            return True

    module = _load_plugin(monkeypatch)
    plugin = module.QQChatPlugin(SimpleNamespace(), {"legacy_chat": False})
    state = SimpleNamespace(db=Database())
    plugin.runtime = SimpleNamespace(_core=SimpleNamespace(app=SimpleNamespace(state=state)))
    monkeypatch.setattr(mahjong, "_root", lambda ctx: tmp_path)
    event = FakeEvent({"message_type": kind, "self_id": "bot", "user_id": "human",
                       "group_id": "group" if kind == "group" else "", "sender": {}})
    event.get_platform_name = lambda: "aiocqhttp"
    event.get_message_str = lambda: "雀魂分析 雀魂牌谱:" + LINK
    event.get_extra = lambda *args: ""
    event.make_result = lambda: SimpleNamespace(message=lambda text: text)
    assert module._QQChatMahjongFilter().filter(event, {})
    from unittest.mock import AsyncMock
    event.send = AsyncMock()
    await plugin.on_mahjong_review(event)
    replies = [call.args[0] for call in event.send.call_args_list]
    assert event.llm_calls == [False] and event.stop_calls == 1
    assert not event.action_calls
    if allowed:
        assert len(replies) == 2 and "尚未连接雀魂账号" in replies[-1]
    else:
        assert replies == []
