"""Loopback-only account form; secrets never enter QQ messages or request logs."""

from __future__ import annotations

import asyncio
import secrets
import socket
import webbrowser
from contextlib import contextmanager, suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.services.mahjong.majsoul_client import OFFICIAL_PAGE, PANEL_URL, MajsoulError, service_for


def account_app(root: Path, service=None) -> FastAPI:
    service = service or service_for(root)
    csrf = secrets.token_urlsafe(32)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

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


class _EmbeddedServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # AstrBot owns process signals; this is a second listener in its event loop.
        yield


class AccountPanel:
    def __init__(self, root: Path):
        self.root = root
        self.server = None
        self.task = None
        self.socket = None

    async def start(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 6190))
            listener.listen(16)
            listener.setblocking(False)
        except BaseException:
            listener.close()
            raise
        self.socket = listener
        self.server = _EmbeddedServer(uvicorn.Config(account_app(self.root), log_level="warning", access_log=False))
        self.task = asyncio.create_task(self.server.serve(sockets=[listener]), name="majsoul-account-panel")

    async def close(self):
        if self.server:
            self.server.should_exit = True
        if self.task:
            try:
                await asyncio.wait_for(self.task, 5)
            except TimeoutError:
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
        if self.socket:
            self.socket.close()
