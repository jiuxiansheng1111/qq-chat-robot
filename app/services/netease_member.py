"""从本地网易云会员桥接读取当前账号有权限访问的歌曲 URL。"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.services.bounded_http import ResponseTooLarge, request_bytes

PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def listen_together(settings: Settings, action="status", **parameters) -> dict:
    """Small authenticated local bridge; credentials never enter chat/tool output."""
    if action not in {"status", "accept", "heartbeat"}:
        raise NeteaseMemberError("无效的一起听操作")
    url, token = _bridge_connection(settings)
    async with httpx.AsyncClient(trust_env=False) as client:
        try:
            response = await request_bytes(client,"POST",url+"/api/listen",
                headers={"Authorization":"Bearer "+token},json={"action":action,**parameters},
                max_bytes=64*1024,timeout_seconds=40)
            data = json.loads(response)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise NeteaseMemberError("网易云账号未登录或已过期，请在本机网易云连接页登录") from exc
            raise NeteaseMemberError("一起听接口暂时不可用，尚不能确认已加入") from exc
        except (httpx.HTTPError,TimeoutError,ValueError) as exc:
            raise NeteaseMemberError("网易云本机连接暂时不可用，尚不能确认已加入") from exc
    if not isinstance(data,dict) or data.get("provider") != "netease_listen" or data.get("code") != 200:
        raise NeteaseMemberError("网易云未接受一起听操作，房间可能已过期或没有权限")
    if data.get("self_invite"):
        raise NeteaseSelfInviteError("机器人和邀请者使用同一个网易云账号，不能作为第二位成员加入。当前账号仍可用于听歌分析；双人一起听需要为机器人登录另一个账号")
    return data


class NeteaseMemberError(RuntimeError):
    """本地账号连接不可用，或需要重新登录。"""


class NeteaseSelfInviteError(NeteaseMemberError):
    """The existing account can fetch audio but cannot join its own invitation."""


def _bridge_connection(settings: Settings) -> tuple[str, str]:
    url = settings.netease_member_bridge_url.strip().rstrip("/")
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise NeteaseMemberError("网易云会员连接地址配置无效。") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or not port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise NeteaseMemberError("网易云会员连接必须使用本机 HTTP 地址和明确端口。")
    token_path = Path(settings.netease_member_token_path)
    if not token_path.is_absolute():
        token_path = PROJECT_ROOT / token_path
    token_path = token_path.resolve()
    if not token_path.is_relative_to((PROJECT_ROOT / "data" / "netease").resolve()):
        raise NeteaseMemberError("网易云会员连接令牌必须位于 data/netease 内。")
    try:
        if not token_path.is_file() or token_path.stat().st_size > 256:
            raise NeteaseMemberError("网易云会员连接尚未启动，请运行 start_netease_member.ps1。")
        token = token_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise NeteaseMemberError("网易云会员连接令牌无法读取，请重新启动本机连接。") from exc
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise NeteaseMemberError("网易云会员连接令牌无效，请重新启动本机连接。")
    return url, token


async def get_member_song_payload(
    client: httpx.AsyncClient, settings: Settings, song_id: str
) -> dict:
    """账号 Cookie 留在本地桥接中；这里只接收所选歌曲的 URL。"""
    if not re.fullmatch(r"\d{1,20}", song_id):
        raise NeteaseMemberError("网易云歌曲 ID 无效。")
    url, token = _bridge_connection(settings)
    try:
        data = await request_bytes(
            client, "POST", f"{url}/api/song/url",
            headers={"Authorization": f"Bearer {token}"},
            json={"song_id": song_id},
            timeout_seconds=max(40, settings.music_timeout_seconds),
            max_bytes=256 * 1024,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            raise NeteaseMemberError(
                "网易云账号尚未登录或登录已过期，请在本机会员连接页面重新扫码。"
            ) from exc
        raise NeteaseMemberError("网易云会员音源暂时无法读取，请检查本机登录状态。") from exc
    except ResponseTooLarge as exc:
        raise NeteaseMemberError("网易云会员音源返回内容过大。") from exc
    except (httpx.HTTPError, TimeoutError) as exc:
        raise NeteaseMemberError("网易云会员本机连接不可用，请运行 start_netease_member.ps1。") from exc
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise NeteaseMemberError("网易云会员连接返回格式无效。") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("provider") != "netease_account"
        or str(payload.get("song_id")) != song_id
    ):
        raise NeteaseMemberError("网易云会员连接返回了不匹配的歌曲。")
    return payload
