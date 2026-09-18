from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.providers import OpenAICompatibleProvider
from app.main import app
from app.plugins.media import random_image, random_real_pig_image

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def live_client() -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


def require(value: str, field_name: str) -> str:
    if not value.strip():
        pytest.fail(f"{field_name} 未填写")
    return value


def test_local_health_live(live_client: TestClient):
    response = live_client.get("/health/live")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


def test_local_health_ready(live_client: TestClient):
    response = live_client.get("/health/ready")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"


def test_local_health_llm(live_client: TestClient):
    response = live_client.get("/health/llm")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


def test_admin_jwt_login_refresh_logout(
    live_client: TestClient,
    live_settings: Settings,
):
    username = require(live_settings.admin_username, "ADMIN_USERNAME")
    password = require(live_settings.admin_password, "ADMIN_PASSWORD")
    login = live_client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200, "管理员登录失败，请检查 ADMIN_USERNAME/ADMIN_PASSWORD"
    first = login.json()
    access_token = first["access_token"]
    refresh_token = first["refresh_token"]

    me = live_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me.status_code == 200, me.text

    refresh = live_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh.status_code == 200, refresh.text
    rotated = refresh.json()
    assert rotated["refresh_token"] != refresh_token

    logout = live_client.post(
        "/api/auth/logout",
        json={"refresh_token": rotated["refresh_token"]},
    )
    assert logout.status_code == 200, logout.text


def test_onebot_webhook_token_auth(
    live_client: TestClient,
    live_settings: Settings,
):
    token = require(live_settings.onebot_webhook_token, "ONEBOT_WEBHOOK_TOKEN")
    response = live_client.post(
        "/onebot/webhook",
        headers={"Authorization": f"Bearer {token}"},
        json={"post_type": "meta_event", "meta_event_type": "heartbeat"},
    )
    assert response.status_code == 200, response.text
    assert response.json().get("ignored") is True


async def test_zhipu_llm_api(live_settings: Settings):
    api_key = require(live_settings.llm_api_key, "LLM_API_KEY")
    provider = OpenAICompatibleProvider(
        live_settings.llm_provider,
        live_settings.llm_base_url,
        api_key,
        live_settings.llm_model,
        live_settings.llm_timeout_seconds,
        live_settings.llm_max_output_tokens,
        0,
    )
    try:
        result = await provider.chat(
            [
                {"role": "system", "content": "这是健康检查。"},
                {"role": "user", "content": "只回复 OK"},
            ]
        )
        assert result.strip(), "智谱返回了空内容"
    finally:
        await provider.aclose()


async def test_groq_fallback_api(live_settings: Settings):
    if not live_settings.groq_api_key.strip():
        pytest.skip("GROQ_API_KEY 未填写，备用服务跳过")
    provider = OpenAICompatibleProvider(
        "groq",
        live_settings.groq_base_url,
        live_settings.groq_api_key,
        live_settings.groq_model,
        live_settings.llm_timeout_seconds,
        live_settings.llm_max_output_tokens,
        0,
    )
    try:
        result = await provider.chat(
            [
                {"role": "system", "content": "Health check."},
                {"role": "user", "content": "Reply only OK"},
            ]
        )
        assert result.strip(), "Groq 返回了空内容"
    finally:
        await provider.aclose()


async def test_cat_image_api(live_settings: Settings):
    require(live_settings.cat_api_key, "CAT_API_KEY")
    image = await random_image(
        require(live_settings.cat_api_url, "CAT_API_URL"),
        live_settings.cat_api_key,
        live_settings,
    )
    assert image.startswith(("https://", "base64://"))


async def test_pig_image_api(live_settings: Settings):
    image = await random_real_pig_image(live_settings.pig_api_url, live_settings)
    assert image.url.startswith("base64://")
    assert image.source_url.startswith("https://commons.wikimedia.org/")


async def test_onebot_get_login_info(live_settings: Settings):
    base_url = require(live_settings.onebot_api_base, "ONEBOT_API_BASE")
    expected_id = require(live_settings.onebot_self_id, "ONEBOT_SELF_ID")
    headers = {}
    if live_settings.onebot_access_token:
        headers["Authorization"] = f"Bearer {live_settings.onebot_access_token}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/get_login_info",
                headers=headers,
                json={},
            )
    except httpx.HTTPError as exc:
        pytest.fail(f"OneBot 无法连接：{type(exc).__name__}")
    assert response.status_code < 400, f"OneBot HTTP {response.status_code}"
    payload = response.json()
    assert payload.get("status") in {None, "ok"}, payload.get("wording", "OneBot 返回失败")
    actual_id = str((payload.get("data") or {}).get("user_id", ""))
    assert actual_id, "OneBot 未返回登录 QQ 号"
    assert actual_id == expected_id, "适配器登录 QQ 与 ONEBOT_SELF_ID 不一致"
