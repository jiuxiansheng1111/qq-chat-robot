import asyncio
import logging
import os
from urllib.parse import urlsplit

from app.config import Settings

logger = logging.getLogger("qqchat")

_AUTO_PROXY_CANDIDATES = (
    "http://127.0.0.1:7897",
    "http://127.0.0.1:7890",
    "http://127.0.0.1:7891",
    "http://host.docker.internal:7897",
    "http://host.docker.internal:7890",
    "http://host.docker.internal:7891",
)
_cached_proxy_key: tuple[str, bool] | None = None
_cached_proxy_value: str | None = None
_proxy_lock = asyncio.Lock()


def _environment_proxy() -> str:
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


async def _proxy_port_open(proxy_url: str, timeout: float = 0.35) -> bool:
    try:
        parsed = urlsplit(proxy_url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return False
    if not host or not port:
        return False

    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        return True
    except (OSError, TimeoutError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass


async def resolve_web_proxy(settings: Settings) -> str | None:
    """解析网页搜索和媒体请求要用的出站 HTTP 代理。

    优先级：
    1. `.env` 中的 WEB_PROXY_URL。
    2. 标准 HTTP(S)_PROXY/ALL_PROXY 环境变量。
    3. 主机和 Docker 网关上的常见 Clash Verge/Clash 混合端口。

    使用 HTTP CONNECT 代理时，由代理解析远程主机名，也就不依赖机器人进程能否正常使用公共 DNS。
    """
    global _cached_proxy_key, _cached_proxy_value

    explicit = str(getattr(settings, "web_proxy_url", "") or "").strip()
    auto_detect = bool(getattr(settings, "web_proxy_auto_detect", True))
    key = (explicit, auto_detect)
    if _cached_proxy_key == key:
        return _cached_proxy_value

    async with _proxy_lock:
        if _cached_proxy_key == key:
            return _cached_proxy_value

        proxy = explicit or _environment_proxy()
        if proxy:
            _cached_proxy_key = key
            _cached_proxy_value = proxy
            logger.info("Outbound web traffic using configured proxy: %s", proxy)
            return proxy

        if auto_detect:
            for candidate in _AUTO_PROXY_CANDIDATES:
                if await _proxy_port_open(candidate):
                    _cached_proxy_key = key
                    _cached_proxy_value = candidate
                    logger.info("Auto-detected outbound web proxy: %s", candidate)
                    return candidate

        _cached_proxy_key = key
        _cached_proxy_value = None
        logger.info("No outbound web proxy detected; using system route/direct DNS")
        return None


async def outbound_httpx_kwargs(settings: Settings) -> dict[str, object]:
    proxy = await resolve_web_proxy(settings)
    if proxy:
        return {"proxy": proxy, "trust_env": False}
    return {"trust_env": True}


def reset_proxy_cache_for_tests() -> None:
    global _cached_proxy_key, _cached_proxy_value
    _cached_proxy_key = None
    _cached_proxy_value = None


async def install_outbound_proxy_environment(settings: Settings) -> str | None:
    """把解析出的代理写入标准 HTTP(S)_PROXY 环境变量。

    项目中大多数 httpx 客户端保留 trust_env=True，因此启动时设置一次即可路由 Bing/百度/维基百科/官方媒体和 LLM 的 HTTP 请求，无需逐个修改客户端。本地 OneBot 请求显式使用 trust_env=False，仍直接发送。
    """
    proxy = await resolve_web_proxy(settings)
    if not proxy:
        return None
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if not os.environ.get(key):
            os.environ[key] = proxy
    return proxy
