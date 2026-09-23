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
    """Resolve one outbound HTTP proxy for web search/media traffic.

    Priority:
    1. WEB_PROXY_URL from .env.
    2. Standard HTTP(S)_PROXY/ALL_PROXY environment variables.
    3. Common Clash Verge/Clash mixed ports on host and Docker host gateway.

    An HTTP CONNECT proxy resolves remote hostnames on the proxy side, which also
    avoids relying on the bot process having working public DNS.
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
