from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.auth import auth_dependency

router = APIRouter(prefix="/api/admin", tags=["admin"])


class GroupSettingsUpdate(BaseModel):
    enabled: bool


class BlacklistUpdate(BaseModel):
    blocked: bool


class PluginUpdate(BaseModel):
    enabled: bool


def require_admin(user: Annotated[dict, Depends(auth_dependency)]) -> dict:
    if user.get("role") not in {"admin", "owner"}:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


@router.get("/groups/{group_id}")
async def get_group_settings(group_id: str, request: Request, _: Annotated[dict, Depends(require_admin)]):
    return {"group_id": group_id, "enabled": await request.app.state.db.group_enabled(group_id)}


@router.put("/groups/{group_id}")
async def update_group_settings(
    group_id: str,
    payload: GroupSettingsUpdate,
    request: Request,
    _: Annotated[dict, Depends(require_admin)],
):
    await request.app.state.db.set_group_enabled(group_id, payload.enabled)
    return {"group_id": group_id, "enabled": payload.enabled}


@router.put("/groups/{group_id}/blacklist/{user_id}")
async def update_blacklist(
    group_id: str,
    user_id: str,
    payload: BlacklistUpdate,
    request: Request,
    _: Annotated[dict, Depends(require_admin)],
):
    await request.app.state.db.set_blocked(group_id, user_id, payload.blocked)
    return {"group_id": group_id, "user_id": user_id, "blocked": payload.blocked}


@router.get("/groups/{group_id}/blacklist/{user_id}")
async def get_blacklist_status(
    group_id: str,
    user_id: str,
    request: Request,
    _: Annotated[dict, Depends(require_admin)],
):
    return {
        "group_id": group_id,
        "user_id": user_id,
        "blocked": await request.app.state.db.is_blocked(group_id, user_id),
    }


@router.put("/groups/{group_id}/plugins/{plugin_name}")
async def update_plugin(
    group_id: str,
    plugin_name: str,
    payload: PluginUpdate,
    request: Request,
    _: Annotated[dict, Depends(require_admin)],
):
    await request.app.state.db.set_plugin_enabled(group_id, plugin_name, payload.enabled)
    return {"group_id": group_id, "plugin_name": plugin_name, "enabled": payload.enabled}


@router.get("/groups/{group_id}/plugins/{plugin_name}")
async def get_plugin(
    group_id: str,
    plugin_name: str,
    request: Request,
    _: Annotated[dict, Depends(require_admin)],
):
    return {
        "group_id": group_id,
        "plugin_name": plugin_name,
        "enabled": await request.app.state.db.plugin_enabled(group_id, plugin_name),
    }
