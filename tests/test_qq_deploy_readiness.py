"""Deployment readiness must include a logged-in QQ transport."""

import json
from subprocess import CompletedProcess
from unittest.mock import Mock

import httpx
import pytest

from scripts import connect_astrbot_qq, deploy_qq_features


@pytest.mark.parametrize(
    ("online", "health", "exit_code", "ready"),
    [(True, "healthy", 0, True), (False, "healthy", 0, False),
     (True, "disconnected", 0, False), (True, "healthy", 1, False)],
)
def test_readiness_requires_login_and_reverse_connection(monkeypatch, online, health, exit_code, ready):
    account = connect_astrbot_qq.OneBotAccount("primary", "http://127.0.0.1:3000", "test-token", "12345")
    monkeypatch.setattr(connect_astrbot_qq, "load_onebot_accounts", lambda *_args, **_kwargs: [account])

    def reply(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        data = {"user_id": 12345} if request.url.path == "/get_login_info" else {"online": online, "good": online}
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": data})

    monkeypatch.setattr(connect_astrbot_qq, "_new_http_client", lambda: httpx.Client(transport=httpx.MockTransport(reply)))
    probe = Mock(return_value=CompletedProcess([], exit_code, stdout=json.dumps({"Status": health}).encode()))
    monkeypatch.setattr(deploy_qq_features, "_run_astrbot_health_probe", probe)
    result = deploy_qq_features.qq_connection_status()
    probe.assert_called_once_with()
    assert result["identity_matches"] is True
    assert result["napcat_online"] is online
    assert result["reverse_ws_connected"] is (exit_code == 0 and health == "healthy")
    assert all(result.values()) is ready


def test_another_qq_account_cannot_pass_readiness(monkeypatch):
    account = connect_astrbot_qq.OneBotAccount("primary", "http://127.0.0.1:3000", "test-token", "12345")
    monkeypatch.setattr(connect_astrbot_qq, "load_onebot_accounts", lambda *_args, **_kwargs: [account])
    monkeypatch.setattr(connect_astrbot_qq, "_new_http_client", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"status": "ok", "data": {"user_id": 67890}}),
    )))
    assert deploy_qq_features.qq_connection_status() == {
        "identity_matches": False, "napcat_online": False, "reverse_ws_connected": False,
    }
