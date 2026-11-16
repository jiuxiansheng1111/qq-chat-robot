"""Exercise the real HTTP endpoints against an isolated database and temporary credentials."""
import secrets
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings


@pytest.fixture
def api_settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.chdir(tmp_path)
    # The token issuer and the FastAPI verification dependency share this settings instance.
    for field, value in {
        "app_env": "test", "database_path": str(tmp_path / "api.db"),
        "admin_username": "test-admin", "admin_password": secrets.token_urlsafe(24),
        "jwt_secret_key": secrets.token_urlsafe(32), "onebot_webhook_token": secrets.token_urlsafe(24),
        "onebot_api_base": "", "redis_url": "", "daily_news_enabled": False,
        "web_proxy_url": "", "web_proxy_auto_detect": False,
    }.items():
        monkeypatch.setattr(main.settings, field, value)
    monkeypatch.setattr(main, "maintain_cat_gif_cache", AsyncMock())
    return main.settings


@pytest.fixture
def api_client(api_settings) -> Iterator[TestClient]:
    with TestClient(main.app) as client:
        yield client


def test_local_health_live(api_client: TestClient):
    response = api_client.get("/health/live")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


def test_local_health_ready(api_client: TestClient):
    response = api_client.get("/health/ready")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"


def test_local_health_llm(api_client: TestClient):
    response = api_client.get("/health/llm")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


def test_admin_jwt_login_refresh_logout(
    api_client: TestClient,
    api_settings: Settings,
):
    username = api_settings.admin_username
    password = api_settings.admin_password
    login = api_client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200, "管理员登录失败，请检查 ADMIN_USERNAME/ADMIN_PASSWORD"
    first = login.json()
    access_token = first["access_token"]
    refresh_token = first["refresh_token"]

    me = api_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me.status_code == 200, me.text

    refresh = api_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh.status_code == 200, refresh.text
    rotated = refresh.json()
    assert rotated["refresh_token"] != refresh_token

    logout = api_client.post(
        "/api/auth/logout",
        json={"refresh_token": rotated["refresh_token"]},
    )
    assert logout.status_code == 200, logout.text


def test_onebot_webhook_token_auth(
    api_client: TestClient,
    api_settings: Settings,
):
    token = api_settings.onebot_webhook_token
    response = api_client.post(
        "/onebot/webhook",
        headers={"Authorization": f"Bearer {token}"},
        json={"post_type": "meta_event", "meta_event_type": "heartbeat"},
    )
    assert response.status_code == 200, response.text
    assert response.json().get("ignored") is True
