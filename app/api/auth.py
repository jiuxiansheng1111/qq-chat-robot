from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.config import get_settings
from app.core.auth import current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])
auth_dependency = current_user(get_settings())


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


def service(request):
    return request.app.state.auth


@router.post("/login")
async def login(payload: LoginRequest, request: Request):
    return await service(request).login(payload.username, payload.password)


@router.post("/refresh")
async def refresh(payload: RefreshRequest, request: Request):
    return await service(request).refresh(payload.refresh_token)


@router.post("/logout")
async def logout(payload: RefreshRequest, request: Request):
    await service(request).logout(payload.refresh_token)
    return {"ok": True}


@router.get("/me")
async def me(user: Annotated[dict, Depends(auth_dependency)]):
    return user
