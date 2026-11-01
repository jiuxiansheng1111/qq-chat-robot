"""Local CN account, official read-only replay RPC, no browser credential extraction."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import time
import uuid
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import websockets

from app.chatgpt_subscription import CredentialStore, SubscriptionError
from app.services.mahjong.liqi import MAX_BINARY, decode, encode, record_actions
from app.services.mahjong.records import RecordError, share_link

OFFICIAL_PAGE = "https://game.maj-soul.com/1/"
PANEL_URL = "http://127.0.0.1:6190"
ASSET_BASE = "https://game.maj-soul.com/assetbundles/ASTC/"
_TRANSPORT_LOG = logging.getLogger("qqchat.majsoul.transport")
_TRANSPORT_LOG.setLevel(logging.WARNING)


class MajsoulError(RecordError):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ClientVersion:
    resource: str
    package: str

    @property
    def wire(self):
        return "WebGL_2022-" + self.resource


def link_identity(link: str) -> tuple[str, int | None]:
    parts = share_link(link).split("_")
    record = parts[0]
    if len(parts) >= 3 and parts[2] == "2":
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
        record = "".join(alphabet[(alphabet.index(char) + 55 - i) % 36]
                         if char in alphabet else char for i, char in enumerate(record))
    if not re.fullmatch(r"(?:\d{6}-)?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", record):
        raise MajsoulError("牌谱编号格式不正确，请复制雀魂里的完整分享链接")
    account = None
    if len(parts) >= 2 and parts[1]:
        value = parts[1]
        if re.fullmatch(r"a\d{1,12}", value):
            decoded = ((int(value[1:]) - 1358437) ^ 86216345) - 1117113
            if decoded < 0 or decoded % 7:
                raise MajsoulError("牌谱玩家视角编码不正确")
            account = decoded // 7
        elif re.fullmatch(r"\d{1,12}", value):
            account = int(value)
        else:
            raise MajsoulError("牌谱玩家视角编码不正确")
    return record, account


def password_hash(password: str) -> str:
    return hmac.new(b"lailai", password.encode("utf-8"), hashlib.sha256).hexdigest()


class MajsoulRPC:
    def __init__(self, socket):
        self.socket = socket
        self.serial = 0

    async def call(self, method: str, request: str, response: str, values: dict) -> dict:
        if method not in {"login", "fetchGameRecord", "readGameRecord", "requestConnection"}:
            raise ValueError("Unsupported RPC")
        self.serial += 1
        serial = self.serial.to_bytes(2, "little")
        service = "Route" if method == "requestConnection" else "Lobby"
        wrapper = encode("Wrapper", {"name": f".lq.{service}." + method, "data": encode(request, values)})
        await self.socket.send(b"\x02" + serial + wrapper)
        async with asyncio.timeout(20):
            while True:
                frame = await self.socket.recv()
                if not isinstance(frame, bytes) or len(frame) < 3:
                    raise MajsoulError("雀魂返回了无效响应")
                if frame[0] != 3 or frame[1:3] != serial:
                    continue
                result = decode(response, decode("Wrapper", frame[3:])["data"])
                code = result.get("error", {}).get("code", 0)
                if code:
                    if code == 151:
                        raise MajsoulError("雀魂拒绝了客户端版本或连接参数（错误码 151）。这不能说明账号未注册或密码错误，请更新机器人接入后重试。", code)
                    action = "登录" if method == "login" else "读取牌谱"
                    raise MajsoulError(f"雀魂{action}未通过（错误码 {code}）。请在本机账号页检查登录；需要验证码时到官网处理。", code)
                return result


async def _json(client, url: str):
    response = await client.get(url)
    response.raise_for_status()
    if len(response.content) > 512 * 1024:
        raise MajsoulError("雀魂服务配置异常")
    return response.json()


async def _bounded_get(client, url: str, limit: int) -> bytes:
    async with client.stream("GET", url, headers={"Cache-Control": "no-cache"}) as response:
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > limit:
                raise MajsoulError("雀魂版本配置超过大小上限")
        return bytes(data)


async def current_client_version(client) -> ClientVersion:
    page = await _bounded_get(client, OFFICIAL_PAGE + "index.html", 512 * 1024)
    package = re.search(rb'productVersion:\s*"(\d+(?:\.\d+){2})"', page)
    if not package:
        raise MajsoulError("无法确认雀魂当前客户端版本，未尝试使用旧版参数登录")
    # The legacy version.json only describes the retired web resources. Unity's
    # current resource version is published in its own version asset bundle.
    manifest = await _bounded_get(client, ASSET_BASE + "bundle_info_so.majset", 4 * 1024 * 1024)
    bundle = re.search(rb'td1bd8bb002s_[0-9a-f]{20}', manifest)
    if not bundle:
        raise MajsoulError("无法找到雀魂当前资源版本，请稍后重试")
    name = bundle[0].decode("ascii")
    for prefix in ("u6v_", ""):
        try:
            content = await _bounded_get(client, ASSET_BASE + prefix + name + ".majset", 64 * 1024)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                continue
            raise
        resource = re.search(rb':\s*"(\d+(?:\.\d+){2})"\s*,\s*"platform"\s*:\s*"WebGL"', content)
        if resource:
            return ClientVersion(resource[1].decode("ascii"), package[1].decode("ascii"))
    raise MajsoulError("无法解析雀魂当前资源版本，未尝试使用旧版参数登录")


async def server_settings(client) -> tuple[ClientVersion, list[str]]:
    version = await _json(client, OFFICIAL_PAGE + "version.json")
    resource = version.get("version", "")
    if not re.fullmatch(r"\d+(?:\.\d+){2}\.w", resource):
        raise MajsoulError("无法确认雀魂客户端版本")
    client_version = await current_client_version(client)
    # Only this official discovery endpoint is contacted. Returned domains are pinned below.
    routes = await _json(client, "https://route-2.maj-soul.com/api/clientgate/routes?platform=Web&version="
                         + resource + "&lang=chs&randv=" + str(int(time.time())))
    gateways = []
    for item in routes.get("data", {}).get("routes", []):
        domain = item.get("domain", "")
        if (re.fullmatch(r"route-[2-6]\.maj-soul\.com(?::(?:443|8443))?", domain)
                and item.get("ssl") is True and item.get("state") != "maintain"):
            gateways.append("wss://" + domain + "/gateway")
    if not gateways:
        raise MajsoulError("雀魂当前没有可用的官方读取线路")
    return client_version, gateways[:3]


@asynccontextmanager
async def official_session(account: str, hashed_password: str):
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            version, gateways = await server_settings(client)
            socket = None
            for gateway in gateways:
                try:
                    socket = await websockets.connect(gateway, origin="https://game.maj-soul.com",
                                                      open_timeout=8, close_timeout=2,
                                                      max_size=MAX_BINARY, logger=_TRANSPORT_LOG)
                    break
                except (OSError, TimeoutError, websockets.WebSocketException):
                    continue
            if socket is None:
                raise MajsoulError("连接雀魂官方服务失败，请稍后重试")
            try:
                rpc = MajsoulRPC(socket)
                route = urlsplit(gateway).hostname.split(".")[0]
                await rpc.call("requestConnection", "ReqRouteConnection", "ResCommon", {
                    "kind": 1, "route_id": route, "timestamp": int(time.time() * 1000), "platform": "Web",
                })
                await rpc.call("login", "ReqLogin", "ResLogin", {
                    "account": account, "password": hashed_password, "type": 0, "tag": "cn",
                    "reconnect": False, "random_key": str(uuid.uuid4()), "gen_access_token": False,
                    "device": {"platform": "pc", "hardware": "pc", "os": "windows",
                               "is_browser": True, "software": "Chrome", "sale_platform": "web"},
                    "client_version": {"resource": version.resource, "package": version.package},
                    "client_version_string": version.wire,
                    "currency_platforms": [1, 2, 5, 6, 8, 10, 11],
                })
                yield rpc, client, version.wire
            finally:
                await socket.close()
    except MajsoulError:
        raise
    except (httpx.HTTPError, OSError, TimeoutError, websockets.WebSocketException) as exc:
        raise MajsoulError("雀魂连接或读取超时；没有取得完整牌谱，请稍后重试") from exc


class MajsoulService:
    def __init__(self, root: Path):
        self.root = root
        self.store = CredentialStore(root / "data/mahjong/account")
        self.lock = asyncio.Lock()
        self.last_opened = 0.0
        self.last_error = ""

    def _saved(self):
        try:
            return self.store.read()["profiles"].get("majsoul", {})
        except SubscriptionError as exc:
            raise MajsoulError("本机雀魂凭据无法读取，请在账号设置页重新保存") from exc

    def status(self):
        saved = self._saved()
        account = saved.get("account", "")
        return {"configured": bool(account), "account_hint": (account[:2] + "***" + account[-2:]) if account else "",
                "auto_open": saved.get("auto_open", True), "error": self.last_error}

    async def save(self, account: str, password: str, auto_open: bool):
        account = account.strip()
        if not 1 <= len(account) <= 254 or not 1 <= len(password) <= 256:
            raise MajsoulError("请填写有效的雀魂国服账号和密码")
        hashed = password_hash(password)
        async with self.lock:
            try:
                async with official_session(account, hashed):
                    pass
                async with self.store.locked():
                    self.store.write({"profiles": {"majsoul": {"account": account, "password_hash": hashed,
                                                               "auto_open": bool(auto_open)}}})
                self.last_error = ""
            except MajsoulError as exc:
                self.last_error = str(exc)
                raise

    async def forget(self):
        async with self.lock, self.store.locked():
            self.store.write({"profiles": {}})
            self.last_error = ""

    async def fetch(self, link: str) -> tuple[dict, list[dict], int | None]:
        record_id, target_id = link_identity(link)
        saved = self._saved()
        if not saved:
            raise MajsoulError("尚未连接雀魂账号。请机器人管理员打开本机的‘雀魂账号设置’，保存并验证账号后重试；不需要截图。")
        if self.lock.locked():
            raise MajsoulError("正在登录或读取另一份牌谱，请稍后再试")
        async with self.lock:
            if saved.get("auto_open") and time.monotonic() - self.last_opened >= 60:
                self.last_opened = time.monotonic()
                await asyncio.to_thread(webbrowser.open, link)
            try:
                async with official_session(saved["account"], saved["password_hash"]) as (rpc, client, version):
                    values = {"game_uuid": record_id, "client_version_string": version}
                    try:
                        response = await rpc.call("fetchGameRecord", "ReqGameRecord", "ResGameRecord", values)
                    except MajsoulError as exc:
                        if exc.code != 151:
                            raise
                        await rpc.call("readGameRecord", "ReqGameRecord", "ResGameRecord", values)
                        response = await rpc.call("fetchGameRecord", "ReqGameRecord", "ResGameRecord", values)
                    head = response.get("head", {})
                    if head.get("uuid") != record_id or not head.get("end_time"):
                        raise MajsoulError("返回牌谱编号不符或对局未结束，不能做赛后整场分析")
                    payload = response.get("data", b"")
                    if not payload and response.get("data_url"):
                        url = urlsplit(response["data_url"])
                        if (url.scheme != "https" or url.hostname not in {"record-old.maj-soul.com", "record-v2.maj-soul.com"}
                                or url.username or url.password or url.port not in (None, 443, 9443, 5333)):
                            raise MajsoulError("旧牌谱下载地址不在已验证的官方来源内")
                        async with client.stream("GET", response["data_url"]) as stream:
                            stream.raise_for_status()
                            chunks = bytearray()
                            async for chunk in stream.aiter_bytes():
                                chunks.extend(chunk)
                                if len(chunks) > MAX_BINARY:
                                    raise MajsoulError("原始牌谱超过大小上限")
                            payload = bytes(chunks)
                    records = record_actions(payload)
                    self.last_error = ""
                    return head, records, target_id
            except MajsoulError as exc:
                self.last_error = str(exc)
                raise


_services: dict[Path, MajsoulService] = {}


def service_for(root: Path) -> MajsoulService:
    root = root.resolve()
    if root not in _services:
        _services[root] = MajsoulService(root)
    return _services[root]
