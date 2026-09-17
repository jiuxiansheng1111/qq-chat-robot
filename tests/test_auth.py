from fastapi import HTTPException

from app.config import Settings
from app.core.auth import AuthService
from app.db.database import Database


async def test_refresh_token_rotation(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "auth.db"),
        jwt_secret_key="test-secret-key-with-at-least-32-bytes",
        admin_username="admin",
        admin_password="password",
    )
    db = Database(settings)
    await db.init()
    auth = AuthService(settings, db)
    await auth.ensure_admin()

    first = await auth.login("admin", "password")
    second = await auth.refresh(first["refresh_token"])

    assert first["access_token"] != second["access_token"]
    assert first["refresh_token"] != second["refresh_token"]

    try:
        await auth.refresh(first["refresh_token"])
    except HTTPException as exc:
        assert getattr(exc, "status_code", None) == 401
    else:
        raise AssertionError("rotated refresh token must be rejected")
