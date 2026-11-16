"""Account controls for the robot's own modules, served by its local settings page."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.chatgpt_subscription import DEFAULT_MODEL, ChatGPTSubscription, SubscriptionError
from app.config import Settings
from app.services import game_clients
from app.services.local_listener import LocalListener
from app.services.mahjong.majsoul_client import MajsoulError
from app.services.netease_member import NeteaseMemberError, _bridge_connection


class AccountActionError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _minecraft_cache(folder, username):
    # prismarine-auth hashes the lower byte of each UTF-16 code unit (Node 'binary').
    prefix = hashlib.sha1(username.encode("utf-16-le")[::2], usedforsecurity=False).hexdigest()[:6]
    return folder / (prefix + "_mca-cache.json")


def _minecraft_saved(folder, username):
    cache = _minecraft_cache(folder, username)
    if not cache.is_file():
        return False
    session = json.loads(cache.read_text(encoding="utf-8"))
    return bool((session.get("mca") or {}).get("access_token"))


def _subscription_directory(root):
    config = root / "data/astrbot/instance/data/cmd_config.json"
    if config.is_file():
        try:
            sources = json.loads(config.read_text(encoding="utf-8-sig")).get("provider_sources", [])
            for source in sources:
                if (source.get("id") == "qqchat-chatgpt-subscription"
                        and source.get("type") == "qqchat_chatgpt_subscription" and source.get("chatgpt_credentials_dir")):
                    return root / source["chatgpt_credentials_dir"]
        except (ValueError, OSError, AttributeError):
            pass
    return root / "data/chatgpt"


class MinecraftLogin:
    def __init__(self, root, player):
        self.root, self.player = root, player
        self.folder = root / "data/game-companion/mc-auth"
        self.task = None
        self.state, self.code = "idle", ""

    def progress(self):
        result = {"state": self.state}
        if self.state == "device":
            result["code"] = self.code
        return result

    async def start(self):
        async with self.player.lock:
            if self.task and not self.task.done():
                return self.progress()
            if self.player.process and self.player.process.returncode is None:
                raise AccountActionError("大肥鱼正在联机。退服后可以管理这个账号。", 409)
            config = game_clients.settings().get("minecraft") or {}
            node = shutil.which("node")
            helper = self.root / "integrations/minecraft-player/account.cjs"
            if not node or not (helper.parent / "node_modules/prismarine-auth").is_dir():
                raise AccountActionError("本机尚未安装 Minecraft 玩家组件。", 503)
            self.player.authorizing = True
            self.state, self.code = "starting", ""
            self.task = asyncio.create_task(self._run(node, helper, config.get("username") or "Dafeiyu"))
            return self.progress()

    async def _run(self, node, helper, username):
        proc = None
        try:
            async with asyncio.timeout(900):
                proc = await asyncio.create_subprocess_exec(
                    node, str(helper), username, str(self.folder), stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                while line := await proc.stdout.readline():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    state = event.get("state")
                    if state == "device" and re.fullmatch(r"[A-Z0-9-]{4,16}", str(event.get("code", ""))):
                        self.state, self.code = state, event["code"]
                    elif state == "verified":
                        self.state, self.code = state, ""
                if await proc.wait() or self.state != "verified":
                    self.state = "failed"
        except asyncio.CancelledError:
            self.state = "idle"
            raise
        except (OSError, ValueError, TimeoutError):
            self.state = "failed"
        finally:
            self.code = ""
            if proc and proc.returncode is None:
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            self.player.authorizing = False

    async def cancel(self):
        async with self.player.lock:
            if self.task and not self.task.done():
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
            self.player.authorizing = False
            self.state, self.code = "idle", ""
            return self.progress()

    async def forget(self):
        async with self.player.lock:
            if self.player.authorizing or self.player.process and self.player.process.returncode is None:
                raise AccountActionError("请先结束当前登录或退出 Minecraft 服务器。", 409)
            username = (game_clients.settings().get("minecraft") or {}).get("username") or "Dafeiyu"
            if self.folder.resolve() != self.root.resolve() / "data/game-companion/mc-auth":
                raise AccountActionError("账号目录位置发生了变化，无法清除。", 409)
            prefix = _minecraft_cache(self.folder, username).name.partition("_")[0]
            for file in self.folder.glob(prefix + "_*-cache.json"):
                if file.is_file():
                    file.unlink()
            self.state, self.code = "idle", ""
            return {"ok": True}


class ChatLogin:
    """Temporarily own the registered OAuth callback port after a local button click."""

    def __init__(self, directory):
        self.directory = directory
        self.listener = LocalListener(1455)
        self.lock = asyncio.Lock()
        self.progress = {}
        self.timer = None

    async def start(self):
        from scripts.connect_chatgpt import login_app

        async with self.lock:
            if self.listener.server is None:
                subscription = ChatGPTSubscription(self.directory)
                args = SimpleNamespace(store=self.directory, port=1455, model=DEFAULT_MODEL,
                                       activate=False, open_browser=False, new_account=False, profile="")
                app = login_app(subscription, args, fresh=True)
                try:
                    await self.listener.start(app)
                except (OSError, TimeoutError):
                    await subscription.aclose()
                    raise AccountActionError("ChatGPT 的本机授权页未能启动。请先完成或关闭已有的授权页。", 409) from None
                self.progress = app.state.login_progress
                self.timer = asyncio.create_task(self._expire())
            return {"url": "http://127.0.0.1:1455/"}

    async def _expire(self):
        deadline = asyncio.get_running_loop().time() + 900
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(2)
            if self.progress.get("status") == "ready":
                await asyncio.sleep(15)
                break
        async with self.lock:
            await self.listener.close()
            self.timer = None

    async def close(self):
        async with self.lock:
            if self.timer:
                self.timer.cancel()
                with suppress(asyncio.CancelledError):
                    await self.timer
                self.timer = None
            await self.listener.close()


class AccountHub:
    def __init__(self, root, mahjong, player=None):
        self.root, self.mahjong = root, mahjong
        self.settings = Settings(_env_file=root / ".env")
        self.minecraft = MinecraftLogin(root, player or game_clients.MinecraftPlayer())
        self.chat = ChatGPTSubscription(_subscription_directory(root))
        self.chat_login = ChatLogin(self.chat.store.directory)
        self.chat_action = asyncio.Lock()

    async def close(self):
        await self.minecraft.cancel()
        await self.chat_login.close()
        await self.chat.aclose()

    def minecraft_status(self):
        config = game_clients.settings().get("minecraft") or {}
        username = config.get("username") or "Dafeiyu"
        host, port = config.get("host", ""), config.get("port", 25565)
        if ":" in host:
            host = "[" + host + "]"
        server = f"{host}:{port}" if host else ""
        if self.minecraft.player.authorizing:
            state = "pending"
        elif config.get("auth") == "offline":
            state = "offline_server"
        else:
            state = "saved" if _minecraft_saved(self.minecraft.folder, username) else "signed_out"
        return {"state": state, "server": server}

    def steam_status(self):
        account = str((game_clients.settings().get("steam") or {}).get("bot_id") or "")
        _, state = game_clients.steam_client(account)
        return {"state": state, "hint": account[-4:] if account else ""}

    def mahjong_status(self):
        status = self.mahjong.status()
        state = "needs_login" if status.get("error") else "saved" if status.get("configured") else "signed_out"
        return {"state": state, "hint": status.get("account_hint", "")}

    def bilibili_status(self):
        # The project's video tools share the Bilibot account managed by AstrBot.
        config = self.root / "data/astrbot/instance/data/config/astrbot_plugin_bilibili_ai_bot_config.json"
        if not config.is_file():
            installed = (config.parent.parent / "plugins/astrbot_plugin_bilibili_ai_bot").is_dir()
            return {"state": "signed_out" if installed else "not_installed"}
        saved = json.loads(config.read_text(encoding="utf-8-sig"))
        return {"state": "saved" if saved.get("SESSDATA") else "signed_out"}

    def chat_status(self):
        status = self.chat.status()
        progress = self.chat_login.progress.get("status")
        if self.chat_login.listener.server and progress in {"waiting", "authorizing", "verifying"}:
            state = "pending"
        elif progress == "failed":
            state = "verification_failed"
        else:
            state = "saved" if status["connected"] else "signed_out"
        return {"state": state, "stored": bool(status["connected"])}

    async def netease_status(self):
        # Show the sign-in link for the configured local bridge even if its token is missing.
        url = self.settings.netease_member_bridge_url.strip().rstrip("/")
        parts = urlsplit(url)
        if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost"} or not parts.port
                or parts.username is not None or parts.password is not None or parts.path or parts.query or parts.fragment):
            return {"state": "unavailable"}
        info = {"state": "unavailable", "url": f"http://{parts.hostname}:{parts.port}/login"}
        try:
            base, token = _bridge_connection(self.settings)
            async with httpx.AsyncClient(trust_env=False, timeout=4) as client:
                response = await client.post(base + "/api/account/status", headers={"Authorization": "Bearer " + token}, json={})
                response.raise_for_status()
                data = response.json()
            if data.get("provider") == "netease_member_bridge" and isinstance(data.get("logged_in"), bool):
                info["state"] = "online" if data["logged_in"] else "signed_out"
        except (NeteaseMemberError, httpx.HTTPError, ValueError):
            pass
        return info

    async def statuses(self):
        sources = {"minecraft": self.minecraft_status, "steam": self.steam_status,
                   "mahjong": self.mahjong_status, "chatgpt": self.chat_status, "bilibili": self.bilibili_status}
        results = await asyncio.gather(*(asyncio.to_thread(fn) for fn in sources.values()),
                                       self.netease_status(), return_exceptions=True)
        return {key: value if isinstance(value, dict) else {"state": "unavailable"}
                for key, value in zip((*sources, "netease"), results)}

    async def update(self, service, action, data):
        if service == "minecraft":
            if action in {"login", "cancel", "forget", "progress"}:
                if action == "progress":
                    return self.minecraft.progress()
                method = {"login": self.minecraft.start, "cancel": self.minecraft.cancel, "forget": self.minecraft.forget}
                return await method[action]()
            if action == "server":
                endpoint = game_clients.server_address(str(data.get("server") or "").strip())
                if not endpoint:
                    raise AccountActionError("请填写服务器地址，例如 play.example.org:25565。")
                player = self.minecraft.player
                async with player.lock:
                    if player.process and player.process.returncode is None:
                        raise AccountActionError("大肥鱼正在联机，退服后可以更换服务器。", 409)
                    game_clients.set_server(endpoint)
                return {"ok": True}
        if service == "steam" and action == "open":
            if not await asyncio.to_thread(game_clients.open_steam):
                raise AccountActionError("没有找到本机 Steam 客户端。", 503)
            return {"ok": True}
        if service == "steam" and action in {"bind", "unbind"}:
            account = str(data.get("account") or "").strip() if action == "bind" else ""
            if action == "bind" and (not re.fullmatch(r"[0-9]{17}", account) or
                                     not game_clients.STEAM_BASE < int(account) < game_clients.STEAM_BASE + 2**32):
                raise AccountActionError("请填写大肥鱼专用账号的 17 位个人 SteamID。")
            game_clients.set_steam_account(account)
            return {"ok": True}
        if service == "chatgpt":
            async with self.chat_action:
                if action == "login":
                    return await self.chat_login.start()
                if action not in {"cancel", "logout"}:
                    raise AccountActionError("没有这个账号操作。", 404)
                await self.chat_login.close()
                if action == "logout":
                    await self.chat.logout()
                self.chat_login.progress = {}
                return {"ok": True}
        raise AccountActionError("没有这个账号操作。", 404)


def account_routes(root: Path, hub, csrf):
    router = APIRouter()

    @router.get("/accounts", response_class=HTMLResponse)
    async def accounts_page():
        return (root / "integrations/accounts.html").read_text(encoding="utf-8").replace("__CSRF__", csrf)

    @router.get("/api/accounts")
    async def statuses():
        return await hub.statuses()

    @router.post("/api/accounts/{service}/{action}")
    async def update(service: str, action: str, request: Request):
        try:
            body = await request.body()
            if len(body) > 4096:
                raise AccountActionError("请求过大。", 413)
            data = json.loads(body) if body else {}
            if not isinstance(data, dict):
                raise AccountActionError("请从账号页提交表单。")
            return await hub.update(service, action, data)
        except AccountActionError as exc:
            return JSONResponse({"error": str(exc)}, exc.status)
        except (SubscriptionError, MajsoulError, httpx.HTTPError, TimeoutError, OSError):
            return JSONResponse({"error": "账号服务暂时不可用，请稍后再试。"}, 503)
        except (ValueError, TypeError):
            return JSONResponse({"error": "账号设置格式不正确。"}, 400)

    return router
