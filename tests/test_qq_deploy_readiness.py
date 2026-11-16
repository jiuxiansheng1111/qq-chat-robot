"""Deployment readiness must include a logged-in QQ transport."""

from types import SimpleNamespace

import httpx
import pytest

from scripts import connect_astrbot_qq, deploy_qq_features


@pytest.mark.parametrize(
    ("online", "health", "ready"),
    [(True, "healthy", True), (False, "healthy", False), (True, "disconnected", False)],
)
def test_readiness_requires_login_and_reverse_connection(monkeypatch, online, health, ready):
    account = connect_astrbot_qq.OneBotAccount("primary", "http://127.0.0.1:3000", "test-token", "12345")
    monkeypatch.setattr(connect_astrbot_qq, "load_onebot_accounts", lambda *_args, **_kwargs: [account])

    def reply(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        data = {"user_id": 12345} if request.url.path == "/get_login_info" else {"online": online, "good": online}
        return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": data})

    monkeypatch.setattr(connect_astrbot_qq, "_new_http_client", lambda: httpx.Client(transport=httpx.MockTransport(reply)))
    monkeypatch.setattr(deploy_qq_features.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout=(f'{{"Status":"{health}"}}').encode(),
    ))
    result = deploy_qq_features.qq_connection_status()
    assert result["identity_matches"] is True
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
