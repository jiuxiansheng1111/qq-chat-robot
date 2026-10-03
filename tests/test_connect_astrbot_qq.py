"""只测试连接器的本地校验、配置变换和 mock HTTP 契约。"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "connect_astrbot_qq.py"
SPEC = importlib.util.spec_from_file_location("_test_connect_astrbot_qq", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
connect = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = connect
SPEC.loader.exec_module(connect)


def test_load_astrbot_token_requires_one_enabled_local_platform(tmp_path: Path) -> None:
    config_dir = tmp_path / "data" / "astrbot" / "instance" / "data"
    config_dir.mkdir(parents=True)
    path = config_dir / "cmd_config.json"
    path.write_text(
        json.dumps(
            {
                "platform": [
                    {
                        "id": "qq-chatrobot-local",
                        "type": "aiocqhttp",
                        "enable": True,
                        "ws_reverse_host": "127.0.0.1",
                        "ws_reverse_port": 6199,
                        "ws_reverse_token": "private-token",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert connect.load_astrbot_ws_token(tmp_path) == "private-token"

    config = json.loads(path.read_text(encoding="utf-8"))
    config["platform"][0]["enable"] = False
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(connect.ConnectError, match="未按预期启用"):
        connect.load_astrbot_ws_token(tmp_path)


@pytest.mark.parametrize(
    "config, message",
    [
        ({"host": "203.0.113.10", "port": 6099, "token": "x"}, "本机或通配"),
        ({"host": "127.0.0.1", "port": 6099, "token": "x", "enable": False}, "禁用"),
        ({"host": "127.0.0.1", "port": 6099, "token": "x", "totpRequired": True}, "双重验证"),
        ({"host": "127.0.0.1", "port": 6099, "token": "x", "enable2FA": True}, "双重验证"),
        ({"host": "127.0.0.1", "port": 6099, "token": "x", "disableWebUI": True}, "禁用"),
        ({"host": "127.0.0.1", "port": 6099, "token": ""}, "token"),
        ({"host": "webui.example.com", "port": 6099, "token": "x"}, "本机或通配"),
    ],
)
def test_webui_settings_fail_closed_for_remote_disabled_or_two_factor(
    tmp_path: Path, config: dict[str, Any], message: str
) -> None:
    path = tmp_path / "webui.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(connect.ConnectError, match=message):
        connect.load_webui_settings(path)


def test_webui_settings_accept_only_loopback_and_hide_token_in_repr(tmp_path: Path) -> None:
    path = tmp_path / "webui.json"
    path.write_text(
        json.dumps({"host": "127.0.0.1", "port": 6099, "token": "private-token"}),
        encoding="utf-8",
    )

    settings = connect.load_webui_settings(path)

    assert settings.base_url == "http://127.0.0.1:6099"
    assert "private-token" not in repr(settings)

    path.write_text(
        json.dumps({"host": "::1", "port": 6099, "token": "private-token"}),
        encoding="utf-8",
    )
    assert connect.load_webui_settings(path).base_url == "http://[::1]:6099"


@pytest.mark.parametrize(
    "host, expected_base",
    [("0.0.0.0", "http://127.0.0.1:6099"), ("::", "http://[::1]:6099")],
)
def test_webui_wildcard_bindings_are_accessed_only_through_loopback(
    tmp_path: Path, host: str, expected_base: str
) -> None:
    path = tmp_path / "webui.json"
    path.write_text(
        json.dumps({"host": host, "port": 6099, "token": "private-token"}),
        encoding="utf-8",
    )

    settings = connect.load_webui_settings(path)

    assert settings.base_url == expected_base
    assert "private-token" not in repr(settings)


def test_totp_secret_alone_does_not_imply_two_factor_is_enabled(tmp_path: Path) -> None:
    path = tmp_path / "webui.json"
    path.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 6099,
                "token": "private-token",
                "enable2FA": False,
                "totpSecret": "configured-secret-value",
            }
        ),
        encoding="utf-8",
    )

    assert connect.load_webui_settings(path).base_url == "http://127.0.0.1:6099"


def test_env_loader_supports_secondary_api_and_hides_tokens(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ONEBOT_API_BASE=http://127.0.0.1:3000\n"
        "ONEBOT_ACCESS_TOKEN=primary-secret\n"
        "ONEBOT_SELF_ID=12345\n"
        "ONEBOT_API_BASE_2=\n"
        "ONEBOT_ACCESS_TOKEN_2=secondary-secret\n"
        "ONEBOT_SELF_ID_2=67890\n",
        encoding="utf-8",
    )

    accounts = connect.load_onebot_accounts(env_path)

    assert [account.name for account in accounts] == ["primary", "secondary"]
    assert accounts[1].api_base == "http://127.0.0.1:3000"
    assert accounts[1].expected_self_id == "67890"
    assert "secondary-secret" not in repr(accounts[1])
    assert [item.name for item in connect.load_onebot_accounts(env_path, "secondary")] == [
        "secondary"
    ]


def test_auto_skips_an_unconfigured_secondary_account(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ONEBOT_API_BASE=http://127.0.0.1:3000\n"
        "ONEBOT_ACCESS_TOKEN=primary-secret\n"
        "ONEBOT_SELF_ID=12345\n"
        "ONEBOT_API_BASE_2=\n"
        "ONEBOT_ACCESS_TOKEN_2=\n"
        "ONEBOT_SELF_ID_2=\n",
        encoding="utf-8",
    )

    accounts = connect.load_onebot_accounts(env_path, "auto")

    assert [account.name for account in accounts] == ["primary"]
    with pytest.raises(connect.ConnectError, match="secondary.*未配置"):
        connect.load_onebot_accounts(env_path, "secondary")


def test_load_onebot_accounts_rejects_external_api_urls_and_partial_secondary(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ONEBOT_API_BASE=https://example.com:3000\nONEBOT_ACCESS_TOKEN=x\n",
        encoding="utf-8",
    )
    with pytest.raises(connect.ConnectError, match="本机 HTTP"):
        connect.load_onebot_accounts(env_path)

    env_path.write_text(
        "ONEBOT_API_BASE=http://127.0.0.1:3000\n"
        "ONEBOT_ACCESS_TOKEN=x\n"
        "ONEBOT_API_BASE_2=http://127.0.0.1:3001\n",
        encoding="utf-8",
    )
    with pytest.raises(connect.ConnectError, match="secondary.*地址"):
        connect.load_onebot_accounts(env_path)


def _sample_config() -> dict[str, Any]:
    return {
        "network": {
            "httpServers": [{"name": "api", "enable": True, "port": 3000}],
            "httpClients": [
                {
                    "name": "old-webhook",
                    "enable": True,
                    "url": "http://127.0.0.1:8000/onebot/webhook",
                    "token": "old-token",
                },
                {
                    "name": "old-webhook-slash",
                    "enable": True,
                    "url": "http://127.0.0.1:8000/onebot/webhook/",
                },
                {
                    "name": "other-local",
                    "enable": True,
                    "url": "http://127.0.0.1:8000/other",
                },
                {
                    "name": "other-host",
                    "enable": True,
                    "url": "http://localhost:8000/onebot/webhook",
                },
                {
                    "name": "outside",
                    "enable": True,
                    "url": "https://127.0.0.1:8000/onebot/webhook",
                },
            ],
            "httpSseServers": [],
            "websocketServers": [],
            "websocketClients": [
                {
                    "name": "other-client",
                    "enable": True,
                    "url": "ws://127.0.0.1:5000/onebot",
                    "token": "other-token",
                }
            ],
            "plugins": [],
        },
        "musicSignUrl": "",
        "parseMultMsg": False,
    }


def test_build_config_disables_only_old_webhook_and_preserves_other_entries() -> None:
    original = _sample_config()
    updated, summary = connect.build_napcat_config(original, "astrbot-secret")

    assert summary == {
        "http_clients_disabled": 2,
        "websocket_client_added": True,
        "websocket_client_reused": False,
    }
    assert original["network"]["httpClients"][0]["enable"] is True
    old_http = updated["network"]["httpClients"]
    assert [item["enable"] for item in old_http] == [False, False, True, True, True]
    assert old_http[0]["token"] == "old-token"
    assert updated["network"]["httpServers"] == original["network"]["httpServers"]
    assert updated["network"]["websocketClients"][0] == original["network"]["websocketClients"][0]

    target = updated["network"]["websocketClients"][1]
    assert target == {
        "name": "astrbot-qqchat",
        "enable": True,
        "url": "ws://127.0.0.1:6199/ws",
        "messagePostFormat": "array",
        "reportSelfMessage": False,
        "heartInterval": 30000,
        "reconnectInterval": 5000,
        "token": "astrbot-secret",
        "debug": False,
    }


def test_build_config_reuses_its_marked_client_without_duplicates() -> None:
    original = _sample_config()
    first, _ = connect.build_napcat_config(original, "token")
    second, summary = connect.build_napcat_config(first, "rotated-token")

    assert summary == {
        "http_clients_disabled": 0,
        "websocket_client_added": False,
        "websocket_client_reused": True,
    }
    clients = second["network"]["websocketClients"]
    assert len([item for item in clients if item["name"] == "astrbot-qqchat"]) == 1
    assert clients[-1]["token"] == "rotated-token"


@pytest.mark.parametrize(
    "client",
    [
        {"name": "astrbot-qqchat", "url": "ws://127.0.0.1:9000/unknown"},
        {"name": "someone-else", "url": "ws://127.0.0.1:6199/ws"},
    ],
)
def test_build_config_refuses_unknown_websocket_target(client: dict[str, Any]) -> None:
    config = {"network": {"httpClients": [], "websocketClients": [client]}}
    with pytest.raises(connect.ConnectError, match="拒绝"):
        connect.build_napcat_config(config, "token")


class FakeResponse:
    def __init__(self, body: dict[str, Any], status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self.body


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        if url.endswith(connect.WEBUI_LOGIN_PATH):
            return FakeResponse({"data": {"Credential": "private-credential"}})
        if url.endswith("/get_login_info"):
            return FakeResponse(
                {"status": "ok", "retcode": 0, "data": {"user_id": 12345}}
            )
        if url.endswith(connect.GET_CONFIG_PATH):
            return FakeResponse({"data": {"config": "{}"}})
        return FakeResponse({"success": True})


def test_mock_http_contract_hashes_webui_token_and_reads_onebot_identity() -> None:
    client = FakeClient()
    webui = connect.WebUiSettings("http://127.0.0.1:6099", "private-webui-token")

    credential = connect._webui_login(client, webui)
    account = connect.OneBotAccount(
        "primary", "http://127.0.0.1:3000", "onebot-token", "12345"
    )
    self_id = connect._get_onebot_self_id(client, account)

    login_call, onebot_call = client.calls
    expected_hash = hashlib.sha256(b"private-webui-token.napcat").hexdigest()
    assert login_call[0] == "POST"
    assert login_call[1].endswith("/api/auth/login")
    assert login_call[2]["json"] == {"hash": expected_hash}
    assert "private-webui-token" not in json.dumps(login_call[2])
    assert onebot_call[0] == "POST"
    assert onebot_call[1] == "http://127.0.0.1:3000/get_login_info"
    assert onebot_call[2] == {
        "headers": {"Authorization": "Bearer onebot-token"},
        "json": {},
    }
    assert credential == "private-credential"
    assert self_id == "12345"


def test_extract_config_accepts_napcat_json_string_response() -> None:
    config = _sample_config()
    assert connect._extract_config({"data": {"config": json.dumps(config)}}) == config


def test_extract_config_accepts_official_napcat_success_response() -> None:
    config = _sample_config()
    response = {"code": 0, "data": config, "message": "success"}

    assert connect._extract_config(connect._response_body(FakeResponse(response))) == config


class FakeConfigBackend:
    def __init__(self, initial: dict[str, Any], *, mismatch_once: bool = False) -> None:
        self.config = copy.deepcopy(initial)
        self.mismatch_once = mismatch_once
        self.set_count = 0
        self.set_payloads: list[dict[str, Any]] = []

    def factory(self, **kwargs: Any):
        assert kwargs == {"timeout": 10, "trust_env": False}
        return FakeConfigClient(self)


class FakeConfigClient:
    def __init__(self, backend: FakeConfigBackend) -> None:
        self.backend = backend

    def __enter__(self):
        return self

    def __exit__(self, *_args: object):
        return False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        if url.endswith(connect.SET_CONFIG_PATH):
            self.backend.set_count += 1
            payload = kwargs["json"]
            assert set(payload) == {"config"}
            assert isinstance(payload["config"], str)
            self.backend.set_payloads.append(copy.deepcopy(payload))
            config = json.loads(payload["config"])
            if self.backend.mismatch_once and self.backend.set_count == 1:
                self.backend.config = {"network": {"httpClients": [], "websocketClients": []}}
            else:
                self.backend.config = config
            return FakeResponse({"code": 0, "data": None, "message": "success"})
        if url.endswith(connect.GET_CONFIG_PATH):
            return FakeResponse(
                {"code": 0, "data": copy.deepcopy(self.backend.config), "message": "success"}
            )
        raise AssertionError("测试不应请求其他地址")


def _plan(tmp_path: Path, old_config: dict[str, Any], *, port_8000=False):
    return connect.ConnectPlan(
        project_root=tmp_path,
        napcat_config_dir=tmp_path / "napcat",
        account_name="primary",
        self_id="12345",
        webui=connect.WebUiSettings("http://127.0.0.1:6099", "web-token"),
        credential="credential-secret",
        old_config=old_config,
        new_config=connect.build_napcat_config(old_config, "astrbot-token")[0],
        http_clients_disabled=2,
        websocket_client_added=True,
        port_6199_listening=True,
        port_8000_listening=port_8000,
    )


def test_apply_plan_backs_up_and_verifies_with_private_boolean_count_report(
    tmp_path: Path,
) -> None:
    old = _sample_config()
    plan = _plan(tmp_path, old)
    backend = FakeConfigBackend(old)

    report = connect.apply_plan(
        plan,
        client_factory=backend.factory,
        port_probe=lambda _host, port: port == connect.ASTRBOT_WS_PORT,
    )

    assert report["config_verified"] is True
    assert report["rollback_attempted"] is False
    assert json.loads(backend.set_payloads[0]["config"]) == plan.new_config
    backup_root = tmp_path / "data" / "astrbot" / "napcat-backups"
    backup_dirs = list(backup_root.iterdir())
    assert len(backup_dirs) == 1
    assert json.loads((backup_dirs[0] / "onebot-config.json").read_text()) == old
    saved_report = json.loads((backup_dirs[0] / "report.json").read_text())
    assert saved_report == report
    assert all(isinstance(value, (bool, int)) for value in report.values())
    assert "12345" not in json.dumps(report)
    assert "token" not in json.dumps(report).lower()
    assert (tmp_path / "data" / "astrbot" / "active-mode.txt").read_bytes() == b"astrbot\n"
    assert report["active_mode_marker_written"] is True


def test_apply_plan_restores_backup_if_verification_fails(tmp_path: Path) -> None:
    old = _sample_config()
    plan = _plan(tmp_path, old)
    backend = FakeConfigBackend(old, mismatch_once=True)
    marker = tmp_path / "data" / "astrbot" / "active-mode.txt"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"legacy-mode\r\n")

    with pytest.raises(connect.ConnectError, match="已恢复旧配置"):
        connect.apply_plan(
            plan,
            client_factory=backend.factory,
            port_probe=lambda _host, port: port == connect.ASTRBOT_WS_PORT,
        )

    backup_root = tmp_path / "data" / "astrbot" / "napcat-backups"
    backup_dir = next(backup_root.iterdir())
    assert json.loads((backup_dir / "onebot-config.json").read_text()) == old
    report = json.loads((backup_dir / "report.json").read_text())
    assert report["config_verified"] is False
    assert report["rollback_attempted"] is True
    assert report["rollback_verified"] is True
    assert backend.config == old
    assert marker.read_bytes() == b"legacy-mode\r\n"
    assert report["active_mode_marker_written"] is False
    assert report["active_mode_marker_restored"] is False


def test_apply_plan_does_not_create_mode_marker_when_config_verification_fails(
    tmp_path: Path,
) -> None:
    old = _sample_config()
    plan = _plan(tmp_path, old)
    backend = FakeConfigBackend(old, mismatch_once=True)

    with pytest.raises(connect.ConnectError, match="已恢复旧配置"):
        connect.apply_plan(
            plan,
            client_factory=backend.factory,
            port_probe=lambda _host, port: port == connect.ASTRBOT_WS_PORT,
        )

    assert not (tmp_path / "data" / "astrbot" / "active-mode.txt").exists()


def test_apply_plan_restores_original_mode_if_marker_write_fails_midway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _sample_config()
    plan = _plan(tmp_path, old)
    backend = FakeConfigBackend(old)
    marker = tmp_path / "data" / "astrbot" / "active-mode.txt"
    marker.parent.mkdir(parents=True)
    original = b"previous-mode\n"
    marker.write_bytes(original)
    write_marker = connect._write_active_mode

    def write_then_fail(project_root: Path) -> None:
        write_marker(project_root)
        raise OSError("simulated marker write follow-up failure")

    monkeypatch.setattr(connect, "_write_active_mode", write_then_fail)

    with pytest.raises(connect.ConnectError, match="已恢复旧配置"):
        connect.apply_plan(
            plan,
            client_factory=backend.factory,
            port_probe=lambda _host, port: port == connect.ASTRBOT_WS_PORT,
        )

    assert marker.read_bytes() == original
    assert backend.config == old
    report_path = next(
        (tmp_path / "data" / "astrbot" / "napcat-backups").iterdir()
    ) / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["config_verified"] is True
    assert report["active_mode_marker_written"] is False
    assert report["active_mode_marker_restored"] is True
    assert report["rollback_verified"] is True


def test_apply_plan_refuses_while_old_server_port_is_listening(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _sample_config(), port_8000=True)

    with pytest.raises(connect.ConnectError, match="8000.*仍在监听"):
        connect.apply_plan(
            plan,
            client_factory=lambda **_kwargs: pytest.fail("不应联网"),
            port_probe=lambda *_args: pytest.fail("无须探测已确认冲突的端口"),
        )

    assert not (tmp_path / "data" / "astrbot" / "napcat-backups").exists()
