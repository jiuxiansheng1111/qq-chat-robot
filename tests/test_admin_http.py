from fastapi.testclient import TestClient

from app.main import app, settings


def test_admin_can_toggle_group_plugin(tmp_path):
    previous = {
        "database_path": settings.database_path,
        "jwt_secret_key": settings.jwt_secret_key,
        "admin_username": settings.admin_username,
        "admin_password": settings.admin_password,
    }
    settings.database_path = str(tmp_path / "admin-http.db")
    settings.jwt_secret_key = "test-admin-secret-key-with-at-least-32-bytes"
    settings.admin_username = "test-admin"
    settings.admin_password = "test-admin-password-123"
    with TestClient(app) as client:
        try:
            login = client.post(
                "/api/auth/login",
                json={"username": settings.admin_username, "password": settings.admin_password},
            )
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            update = client.put(
                "/api/admin/groups/http-test/plugins/weather",
                headers=headers,
                json={"enabled": False},
            )
            assert update.status_code == 200
            assert update.json()["enabled"] is False

            current = client.get(
                "/api/admin/groups/http-test/plugins/weather", headers=headers
            )
            assert current.status_code == 200
            assert current.json()["enabled"] is False
        finally:
            for name, value in previous.items():
                setattr(settings, name, value)
