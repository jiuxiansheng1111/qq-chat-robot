"""Read an account-authorized song URL from the private, local NetEase bridge."""

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class NeteaseMemberError(RuntimeError):
    """The local account connection is unavailable or needs login."""


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
    """Keep account cookies out of the bot; only receive the selected song's URL."""
    if not re.fullmatch(r"\d{1,20}", song_id):
        raise NeteaseMemberError("网易云歌曲 ID 无效。")
    url, token = _bridge_connection(settings)
    try:
        response = await client.post(
            f"{url}/api/song/url",
            headers={"Authorization": f"Bearer {token}"},
            json={"song_id": song_id},
            timeout=max(40, settings.music_timeout_seconds),
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise NeteaseMemberError("网易云会员本机连接不可用，请运行 start_netease_member.ps1。") from exc
    if response.status_code == 401:
        raise NeteaseMemberError("网易云账号尚未登录或登录已过期，请在本机会员连接页面重新扫码。")
    if response.status_code != 200:
        raise NeteaseMemberError("网易云会员音源暂时无法读取，请稍后重试或检查本机登录状态。")
    if len(response.content) > 256 * 1024:
        raise NeteaseMemberError("网易云会员音源返回内容过大。")
    try:
        payload = response.json()
    except ValueError as exc:
        raise NeteaseMemberError("网易云会员连接返回格式无效。") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("provider") != "netease_account"
        or str(payload.get("song_id")) != song_id
    ):
        raise NeteaseMemberError("网易云会员连接返回了不匹配的歌曲。")
    return payload
