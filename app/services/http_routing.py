import asyncio
import ipaddress
import logging
import os
import time
from urllib.parse import urlsplit
from urllib.request import getproxies

import httpx

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
_cached_proxy_key: tuple[str, bool, str] | None = None
_cached_proxy_value: str | None = None
_cached_proxy_at = 0.0
_proxy_lock = asyncio.Lock()
_routing_settings: Settings | None = None


def _environment_proxy() -> str:
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    proxies = getproxies()
    return proxies.get("https") or proxies.get("http") or ""


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
    global _cached_proxy_key, _cached_proxy_value, _cached_proxy_at

    explicit = str(getattr(settings, "web_proxy_url", "") or "").strip()
    auto_detect = bool(getattr(settings, "web_proxy_auto_detect", True))
    environment = _environment_proxy()
    key = (explicit, auto_detect, environment)

    async with _proxy_lock:
        if _cached_proxy_key == key and time.monotonic() - _cached_proxy_at < 5:
            if _cached_proxy_value is None:
                return None
            if await _proxy_port_open(_cached_proxy_value):
                return _cached_proxy_value

        proxy = explicit or environment
        _cached_proxy_key = key
        _cached_proxy_at = time.monotonic()
        if proxy:
            # 已关闭的系统/环境代理不能再次被 trust_env 重新拾取。
            _cached_proxy_value = proxy if await _proxy_port_open(proxy) else None
            return _cached_proxy_value

        if auto_detect:
            for candidate in _AUTO_PROXY_CANDIDATES:
                if await _proxy_port_open(candidate):
                    _cached_proxy_value = candidate
                    return candidate

        _cached_proxy_value = None
        return None


async def outbound_httpx_kwargs(settings: Settings) -> dict[str, object]:
    return {"transport": ProxyFallbackTransport(settings), "trust_env": False}


def _local_host(host: str) -> bool:
    if host in {"localhost", "host.docker.internal"}:
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or address.is_private
    except ValueError:
        return False


class ProxyFallbackTransport(httpx.AsyncBaseTransport):
    """同一个长连接客户端也能在代理关闭后直连，不重放已发送的请求。"""

    def __init__(self, settings: Settings | None = None, **transport_kwargs):
        self.settings = settings
        self._kwargs = {**transport_kwargs, "trust_env": False}
        self._direct = httpx.AsyncHTTPTransport(**self._kwargs)
        self._proxies: dict[str, httpx.AsyncHTTPTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if _local_host(request.url.host):
            return await self._direct.handle_async_request(request)
        settings = self.settings or _routing_settings or Settings(_env_file=None)
        proxy = await resolve_web_proxy(settings)
        if proxy is None:
            return await self._direct.handle_async_request(request)
        transport = self._proxies.get(proxy)
        if transport is None:
            transport = httpx.AsyncHTTPTransport(proxy=proxy, **self._kwargs)
            self._proxies[proxy] = transport
        original_timeout = request.extensions.get("timeout")
        if isinstance(original_timeout, dict):
            timeout = original_timeout.get("connect")
            request.extensions["timeout"] = {
                **original_timeout, "connect": min(timeout, 3.0) if timeout is not None else 3.0,
            }
        try:
            return await transport.handle_async_request(request)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError):
            # 只在连接阶段失败且正文可重放时回退。读响应超时或 HTTP
            # 错误不能重试，否则可能重复生成/收费；流式上传也不能重放。
            if not isinstance(request.stream, httpx.ByteStream):
                raise
            logger.warning("Proxy connection failed; retrying directly")
            if original_timeout is not None:
                request.extensions["timeout"] = original_timeout
            return await self._direct.handle_async_request(request)
        finally:
            if original_timeout is not None:
                request.extensions["timeout"] = original_timeout

    async def aclose(self) -> None:
        await asyncio.gather(self._direct.aclose(), *(p.aclose() for p in self._proxies.values()))


def outbound_http_client(*, settings: Settings | None = None, **kwargs) -> httpx.AsyncClient:
    """所有外网客户端显式关闭系统代理继承，通过传输层动态选择线路。"""
    if "transport" not in kwargs:
        transport_kwargs = {
            key: kwargs[key] for key in ("verify", "cert", "http1", "http2", "limits")
            if key in kwargs
        }
        proxy = kwargs.pop("proxy", None)
        if proxy:
            settings = (settings or _routing_settings or Settings(_env_file=None)).model_copy(
                update={"web_proxy_url": str(proxy)},
            )
        kwargs["transport"] = ProxyFallbackTransport(settings, **transport_kwargs)
    kwargs["trust_env"] = False
    return httpx.AsyncClient(**kwargs)


def reset_proxy_cache_for_tests() -> None:
    global _cached_proxy_key, _cached_proxy_value, _cached_proxy_at
    _cached_proxy_key = None
    _cached_proxy_value = None
    _cached_proxy_at = 0.0


async def configure_outbound_http(settings: Settings) -> str | None:
    """设置项目请求的线路，不修改 AstrBot 或其他插件的进程环境。"""
    global _routing_settings
    _routing_settings = settings
    reset_proxy_cache_for_tests()
    return await resolve_web_proxy(settings)
