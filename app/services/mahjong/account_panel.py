"""Loopback-only account form; secrets never enter QQ messages or request logs."""

from __future__ import annotations

import asyncio
import secrets
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.services.account_hub import AccountHub, account_routes
from app.services.local_listener import LocalListener
from app.services.mahjong.majsoul_client import OFFICIAL_PAGE, PANEL_URL, MajsoulError, service_for


def account_app(root: Path, service=None, *, player=None, hub=None) -> FastAPI:
    service = service or service_for(root)
    csrf = secrets.token_urlsafe(32)
    hub = hub or AccountHub(root, service, player)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await hub.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.include_router(account_routes(root, hub, csrf))

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.headers.get("host") != "127.0.0.1:6190":
            return JSONResponse({"error": "仅供本机访问"}, 403)
        if request.method != "GET":
            if (request.headers.get("origin") != PANEL_URL
                    or not secrets.compare_digest(request.headers.get("x-qqchat-csrf", ""), csrf)):
                return JSONResponse({"error": "请从本机账号页操作"}, 403)
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                size = 99999
            if not 0 <= size <= 4096 or request.headers.get("transfer-encoding"):
                return JSONResponse({"error": "请求过大"}, 413)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                 "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; "
                                 "script-src 'nonce-" + csrf + "'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (root / "integrations/majsoul/account.html").read_text(encoding="utf-8").replace("__CSRF__", csrf)

    @app.get("/status")
    async def status():
        try:
            return service.status()
        except MajsoulError as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @app.post("/account")
    async def save(request: Request):
        try:
            body = await request.body()
            if len(body) > 4096:
                return JSONResponse({"error": "请求过大"}, 413)
            data = await request.json()
            if (not isinstance(data, dict) or not isinstance(data.get("account"), str)
                    or not isinstance(data.get("password"), str) or type(data.get("auto_open")) is not bool):
                return JSONResponse({"error": "请填写账号、密码和自动打开选项"}, 400)
            await service.save(data["account"], data["password"], data["auto_open"])
            return {"ok": True, **service.status()}
        except MajsoulError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        except (ValueError, UnicodeError):
            return JSONResponse({"error": "表单格式不正确"}, 400)

    @app.post("/forget")
    async def forget():
        await service.forget()
        return {"ok": True, **service.status()}

    @app.post("/open")
    async def open_official():
        await asyncio.to_thread(webbrowser.open, OFFICIAL_PAGE)
        return {"ok": True}

    return app


class AccountPanel(LocalListener):
    def __init__(self, root: Path, player=None):
        super().__init__(6190)
        self.root = root
        self.player = player

    async def start(self):
        await super().start(account_app(self.root, player=self.player))
