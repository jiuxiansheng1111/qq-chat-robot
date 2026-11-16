"""Operate the dedicated game clients and report only observable state."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "data/game-companion.json"
PLAYER = ROOT / "integrations/minecraft-player/player.cjs"
STEAM_BASE = 76561197960265728


def settings():
    try:
        value = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    return value if isinstance(value, dict) else {}


def server_address(value):
    try:
        parts = urlsplit("//" + value)
        host, port = parts.hostname, 25565 if parts.port is None else parts.port
        if (not host or parts.username is not None or parts.password is not None or parts.path or parts.query
                or parts.fragment or not 0 < port < 65536 or not re.fullmatch(r"[a-z0-9.:-]+", host, re.IGNORECASE)):
            return None
        return host, port
    except ValueError:
        return None


def set_server(endpoint):
    config = settings()
    host, port = endpoint
    mc = config.get("minecraft") or {}
    if (mc.get("host"), mc.get("port", 25565)) != endpoint:
        # A new destination gets normal Microsoft authentication and version detection.
        mc = {k: mc[k] for k in ("username",) if mc.get(k)}
    config["minecraft"] = dict(mc, host=host, port=port)
    _save_settings(config)


def set_steam_account(account):
    config = settings()
    config["steam"] = {**(config.get("steam") or {}), "bot_id": account}
    _save_settings(config)


def _save_settings(config):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, dir=CONFIG.parent) as temp:
        temp_path = Path(temp.name)
        try:
            json.dump(config, temp, ensure_ascii=False, indent=2)
        except BaseException:
            temp.close()
            temp_path.unlink(missing_ok=True)
            raise
    try:
        temp_path.replace(CONFIG)
    finally:
        temp_path.unlink(missing_ok=True)


class MinecraftPlayer:
    def __init__(self):
        self.process = None
        self.scope = None
        self.authorizing = False
        self.sequence = 0
        self.lock = asyncio.Lock()

    async def _close_process(self):
        proc, self.process, self.scope = self.process, None, None
        if proc and proc.returncode is None:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()

    async def close(self):
        async with self.lock:
            await self._close_process()

    async def _rpc(self, action, **kwargs):
        self.sequence += 1
        proc = self.process
        request = {"action": action, "id": self.sequence, **kwargs}
        proc.stdin.write((json.dumps(request) + "\n").encode())
        await proc.stdin.drain()
        async with asyncio.timeout(50 if action == "join" else 5):
            while line := await proc.stdout.readline():
                try:
                    result = json.loads(line)
                except ValueError:
                    continue
                if isinstance(result, dict) and result.get("id") == self.sequence:
                    return result
        raise EOFError("minecraft player exited")

    async def command(self, scope, action, target=""):
        async with self.lock:
            if action == "join" and self.authorizing:
                return {"status": "login_in_progress"}
            try:
                if self.process and self.process.returncode is not None:
                    await self._close_process()
                if self.scope and (self.scope != scope or action == "join"):
                    status = await self._rpc("status")
                    if status.get("status") in {"in_world", "respawning"} and self.scope != scope:
                        return {"status": "busy"}
                    if status.get("status") in {"in_world", "respawning"}:
                        return status
                    await self._close_process()
                if action == "join" and not self.process:
                    opts = settings().get("minecraft") or {}
                    if not opts.get("host"):
                        return {"status": "no_server"}
                    auth = opts.get("auth", "microsoft")
                    port = int(opts.get("port", 25565))
                    if auth not in {"microsoft", "offline"} or not (0 < port <= 65535):
                        return {"status": "bad_config"}
                    node = shutil.which("node")
                    if not node or not (PLAYER.parent / "node_modules/mineflayer").is_dir():
                        return {"status": "missing_player"}
                    options = {key: opts[key] for key in ("host", "username", "version") if opts.get(key)}
                    options.update(auth=auth, port=port, profilesFolder=str(CONFIG.parent / "game-companion/mc-auth"))
                    self.process = await asyncio.create_subprocess_exec(node, str(PLAYER),
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self.scope = scope
                    result = await self._rpc("join", options=options)
                elif not self.process:
                    return {"status": "offline"}
                else:
                    result = await self._rpc("status" if action == "join" else action, target=target)
                if result.get("status") not in {"in_world", "respawning", "player_unseen", "need_name"}:
                    # Release a disconnected/missing session so a subsequent join really retries.
                    await self._close_process()
                if action == "leave" and result.get("status") == "offline":
                    result["status"] = "left"
                return result
            except (TimeoutError, EOFError, OSError, ValueError):
                await self._close_process()
                return {"status": "connection_failed"}
            except asyncio.CancelledError:
                await self._close_process()
                raise


def installed_steam():
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            exe = Path(winreg.QueryValueEx(key, "SteamExe")[0])
        if exe.is_absolute() and exe.name.lower() == "steam.exe" and exe.is_file():
            return exe
    except (OSError, ValueError):
        pass
    return None


def open_steam():
    exe = installed_steam()
    if exe is None:
        return False
    subprocess.Popen([str(exe)], cwd=exe.parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return True


def steam_client(bot_id):
    """Only hand off to a running client logged in under this dedicated Steam ID."""
    if not re.fullmatch(r"\d{17}", str(bot_id or "")):
        return None, "no_account"
    if os.name != "nt":
        return None, "no_client"
    import _winapi
    import ctypes
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as active:
            actual = int(winreg.QueryValueEx(active, "ActiveUser")[0])
            pid = int(winreg.QueryValueEx(active, "pid")[0])
        if not actual or not pid:
            return None, "no_client"
        if actual + STEAM_BASE != int(bot_id):
            return None, "wrong_account"
        exe = installed_steam()
        if not exe:
            return None, "no_client"
        # A stale registry entry left after shutdown must not start a different account.
        handle = _winapi.OpenProcess(0x1000, False, pid)
        try:
            size = ctypes.c_ulong(32768)
            path = ctypes.create_unicode_buffer(size.value)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, path, ctypes.byref(size))
            if not ok or not Path(path.value).samefile(exe):
                return None, "no_client"
        finally:
            _winapi.CloseHandle(handle)
        return exe, "ready"
    except (OSError, ValueError):
        return None, "no_client"


async def steam_join(app_id, lobby, inviter):
    config = settings().get("steam") or {}
    exe, status = await asyncio.to_thread(steam_client, config.get("bot_id"))
    if not exe:
        return status
    # Forward the native lobby invite the same way as Steam's Windows URL handler.
    # The Steam client also delivers it to games which are already running.
    def launch():
        uri = f"steam://joinlobby/{app_id}/{lobby}/{inviter}"
        proc = subprocess.run([str(exe), "--", uri],
                              check=False, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "submitted" if proc.returncode == 0 else "unavailable"

    try:
        return await asyncio.to_thread(launch)
    except (OSError, subprocess.TimeoutExpired):
        return "unconfirmed"


async def steam_status(app_id, lobby):
    config = settings().get("steam") or {}
    key, account = config.get("web_api_key"), str(config.get("bot_id") or "")
    if not key or not re.fullmatch(r"\d{17}", account):
        return "unconfirmed"
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            response = await client.get("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
                headers={"x-webapi-key": key}, params={"steamids": account})
            response.raise_for_status()
            players = response.json()["response"]["players"]
        record = next((p for p in players if str(p.get("steamid")) == account), {})
        if str(record.get("gameid")) == app_id:
            return "in_lobby" if str(record.get("lobbysteamid")) == lobby else "in_game"
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    return "unconfirmed"
