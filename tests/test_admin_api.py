from fastapi import HTTPException

from app.api.admin import require_admin


def test_admin_role_guard():
    assert require_admin({"role": "owner"})["role"] == "owner"
    assert require_admin({"role": "admin"})["role"] == "admin"
    try:
        require_admin({"role": "member"})
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("member must not access admin API")
