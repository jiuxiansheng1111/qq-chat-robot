"""使用独立的无头浏览器快速渲染已经装配完成的群日报。"""

from __future__ import annotations

import asyncio
import html
import io
import ipaddress
import math
import os
import re
import shutil
import socket
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from playwright.async_api import Route

MAX_HTML_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_PAGE_HEIGHT = 30_000
MAX_STATIC_IMAGES = 80
MAX_ASSET_BYTES = 4 * 1024 * 1024
MAX_ASSET_CACHE_BYTES = 32 * 1024 * 1024
_CSS_URL = re.compile(r"url\(\s*['\"]?([^)'\"\s]+)", re.IGNORECASE)
_VIEWPORT_DIMENSION = re.compile(r"\b(width|height)\s*=\s*(\d+)", re.IGNORECASE)
_LOCAL_FONTS = """
@font-face {font-family:'Noto Sans SC';src:local('Microsoft YaHei');font-weight:100 900}
@font-face {font-family:'Noto Sans TC';src:local('Microsoft JhengHei');font-weight:100 900}
@font-face {font-family:'Noto Sans JP';src:local('Yu Gothic'),local('Microsoft YaHei');font-weight:100 900}
@font-face {font-family:'Outfit';src:local('Segoe UI');font-weight:100 900}
@font-face {font-family:'Share Tech Mono';src:local('Consolas')}
"""
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: https: http:; font-src data:; connect-src 'none'; "
    "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; "
    "worker-src 'none'; media-src 'none'"
)


class LocalReportRenderError(RuntimeError):
    """本地浏览器无法在资源预算内完成报告渲染。"""


class _ReportAssets(HTMLParser):
    """仅提取模板静态图片地址和视口，不跟随链接或执行脚本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: set[str] = set()
        self.viewport: dict[str, int] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "img" and values.get("src"):
            self.images.add(str(values["src"]))
        if tag == "meta" and str(values.get("name", "")).lower() == "viewport":
            for dimension, value in _VIEWPORT_DIMENSION.findall(str(values.get("content", ""))):
                self.viewport[dimension.lower()] = int(value)


def find_report_browser() -> str | None:
    """查找已安装浏览器，不下载浏览器或读取用户资料。"""
    for root in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if root:
            candidate = Path(root) / "Microsoft/Edge/Application/msedge.exe"
            if candidate.is_file():
                return str(candidate)
    for executable in ("msedge", "chromium", "chromium-browser", "google-chrome"):
        candidate = shutil.which(executable)
        if candidate:
            return candidate
    return None


def _public_asset_url(value: str) -> str | None:
    """限制静态图片为无凭据、标准端口的公网 HTTP(S) 地址。"""
    value = html.unescape(value.strip())
    if value.startswith("//"):
        value = "https:" + value
    if len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80 if parsed.scheme == "http" else 443}
        ):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith((".local", ".localhost", ".internal", ".")):
                return None
        else:
            if not address.is_global:
                return None
        return urlunsplit(
            (parsed.scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, "")
        )
    except ValueError:
        return None


def _number(value: object, default: float) -> float:
    """读取有限数值，避免异常选项绕过页面尺寸预算。"""
    try:
        result = float(str(value))
    except (ValueError, TypeError):
        return default
    return result if math.isfinite(result) else default


def _trusted_template_image(url: str) -> bool:
    """只为官方仓库的固定图片路径启用应用侧代理下载。"""
    parsed = urlsplit(url)
    prefixes = (
        "/gh/SXP-Simon/astrbot_plugin_qq_group_daily_analysis@main/assets/",
        "/gh/SXP-Simon/profile_assets@main/",
    )
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"cdn.jsdmirror.com", "cdn.jsdelivr.net", "fastly.jsdelivr.net"}
        and parsed.port is None
        and parsed.username is None
        and not parsed.query
        and parsed.path.startswith(prefixes)
        and re.fullmatch(r"[A-Za-z0-9_./@-]+\.(?:png|jpg|jpeg|webp|gif)", parsed.path) is not None
        and ".." not in parsed.path.split("/")
    )


def _image_mime(payload: bytes) -> str | None:
    """校验静态素材确为受限尺寸图片，拒绝网页和脚本响应。"""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(payload)) as image:
            mime = {
                "PNG": "image/png",
                "JPEG": "image/jpeg",
                "WEBP": "image/webp",
                "GIF": "image/gif",
            }.get(image.format or "")
            if not mime or image.width * image.height > 16_000_000:
                return None
            image.verify()
            return mime
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


@dataclass(frozen=True)
class _RenderOptions:
    width: int
    height: int
    image_type: Literal["png", "jpeg"]
    quality: int
    full_page: bool
    scale: float
    timeout: float

    @classmethod
    def parse(cls, options: Mapping[str, object], viewport: Mapping[str, int]) -> _RenderOptions:
        nested = options.get("viewport")
        dimensions = nested if isinstance(nested, dict) else {}
        width = viewport.get("width", dimensions.get("width", options.get("viewport_width", 940)))
        height = viewport.get(
            "height", dimensions.get("height", options.get("viewport_height", 900))
        )
        width = int(_number(options.get("width", width), 940))
        height = int(_number(options.get("height", height), 900))
        if not 200 <= width <= 2048 or not 200 <= height <= 4096:
            raise LocalReportRenderError("日报视口超过本地渲染预算")
        scale_level = str(options.get("device_scale_factor_level", "normal"))
        scale = {"normal": 1.0, "high": 1.5, "ultra": 2.0}.get(scale_level, 1.0)
        scale = min(2.0, max(1.0, _number(options.get("device_scale_factor", scale), scale)))
        return cls(
            width=width,
            height=height,
            image_type="png" if options.get("type", "png") == "png" else "jpeg",
            quality=min(100, max(1, int(_number(options.get("quality", 85), 85)))),
            full_page=bool(options.get("full_page", True)),
            scale=scale,
            timeout=min(25.0, max(5.0, _number(options.get("timeout", 25000), 25000) / 1000)),
        )


async def _resolve_static_images(values: set[str]) -> tuple[set[str], list[str]]:
    """解析公网图片域名并固定 IP，避免浏览器访问本机网络或 DNS 重绑定。"""
    urls = sorted(filter(None, (_public_asset_url(value) for value in values)))[:MAX_STATIC_IMAGES]
    hosts = {urlsplit(url).hostname for url in urls}
    approved: set[str] = set()
    rules: list[str] = []
    lookup_slots = asyncio.Semaphore(4)

    async def resolve(host: str | None) -> None:
        if not host:
            return
        try:
            async with lookup_slots, asyncio.timeout(2):
                addresses = await asyncio.get_running_loop().getaddrinfo(
                    host, None, family=socket.AF_INET, type=socket.SOCK_STREAM
                )
            ips = {str(item[4][0]) for item in addresses}
            if not ips or any(not ipaddress.ip_address(value).is_global for value in ips):
                return
            approved.add(host)
            # 浏览器只连接刚校验过的公网地址，不再单独解析域名。
            rules.append(f"MAP {host} {min(ips)}")
        except (OSError, TimeoutError, ValueError):
            return

    await asyncio.gather(*(resolve(host) for host in hosts))
    return {url for url in urls if urlsplit(url).hostname in approved}, rules


def _screenshot_scale(width: int, height: int, desired_scale: float) -> Literal["css", "device"]:
    """先保护整张日报的可读内容，再在像素预算允许时提升清晰度。"""
    if height > MAX_PAGE_HEIGHT or width * height > MAX_IMAGE_PIXELS or width > 4096:
        raise LocalReportRenderError("日报整页尺寸超过本地渲染预算")
    return "device" if width * height * desired_scale**2 <= MAX_IMAGE_PIXELS else "css"


class LocalReportRenderer:
    """串行渲染日报，每次使用隔离的临时浏览器上下文。"""

    def __init__(self, browser_path: str | None = None) -> None:
        self.browser_path = browser_path
        self._slots = asyncio.Semaphore(1)
        self._static_images: dict[str, tuple[str, bytes]] = {}

    async def _cache_template_images(self, values: set[str]) -> None:
        """通过已有出站代理缓存官方素材，不向浏览器开放代理或私网。"""
        import httpx

        from app.services.http_routing import outbound_http_client

        urls = {
            url
            for value in values
            if (url := _public_asset_url(value))
            and _trusted_template_image(url)
            and url not in self._static_images
        }
        if not urls:
            return
        downloads = asyncio.Semaphore(4)
        async with outbound_http_client(timeout=3, follow_redirects=False) as client:

            async def fetch(url: str) -> None:
                try:
                    async with downloads, client.stream("GET", url) as response:
                        if response.status_code != 200:
                            return
                        payload = bytearray()
                        async for chunk in response.aiter_bytes():
                            payload.extend(chunk)
                            if len(payload) > MAX_ASSET_BYTES:
                                return
                    raw = bytes(payload)
                    mime = await asyncio.to_thread(_image_mime, raw)
                    if mime:
                        self._static_images[url] = (mime, raw)
                        while (
                            sum(len(item[1]) for item in self._static_images.values())
                            > MAX_ASSET_CACHE_BYTES
                        ):
                            self._static_images.pop(next(iter(self._static_images)))
                except (httpx.HTTPError, OSError, TimeoutError):
                    # 装饰素材失联不影响正文、统计图和报告投递。
                    return

            try:
                async with asyncio.timeout(7):
                    await asyncio.gather(*(fetch(url) for url in sorted(urls)[:MAX_STATIC_IMAGES]))
            except TimeoutError:
                pass

    async def render(self, content: str, options: Mapping[str, object] | None = None) -> bytes:
        """在有限时间、页面和网络资源预算内把报告 HTML 转为图片。

        Args:
            content: 已完成 Jinja 装配的日报 HTML。
            options: AstrBot 的图片格式、视口、质量与超时选项。

        Returns:
            PNG 或 JPEG 图片字节。

        Raises:
            LocalReportRenderError: 缺少浏览器或超过渲染预算。
            TimeoutError: 排队或渲染超过上限。
        """
        if not content.strip() or len(content.encode("utf-8")) > MAX_HTML_BYTES:
            raise LocalReportRenderError("日报 HTML 为空或超过本地渲染预算")
        assets = _ReportAssets()
        assets.feed(content)
        assets.images.update(_CSS_URL.findall(content))
        literal_image_urls = {url for value in assets.images if (url := _public_asset_url(value))}
        settings = _RenderOptions.parse(options or {}, assets.viewport)
        executable = self.browser_path or find_report_browser()
        if not executable:
            raise LocalReportRenderError("未找到已安装的 Edge 或 Chromium 浏览器")
        from playwright.async_api import async_playwright

        async with asyncio.timeout(35), self._slots:
            async with asyncio.timeout(settings.timeout):
                await self._cache_template_images(assets.images)
                allowed_urls, resolver_rules = await _resolve_static_images(assets.images)
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(
                        executable_path=executable,
                        headless=True,
                        timeout=8000,
                        args=[
                            "--no-proxy-server",
                            "--disable-background-networking",
                            "--disable-component-update",
                            "--disable-sync",
                            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                            "--host-resolver-rules="
                            + ",".join(resolver_rules + ["MAP * ~NOTFOUND"]),
                        ],
                    )
                    try:
                        context = await browser.new_context(
                            viewport={"width": settings.width, "height": settings.height},
                            device_scale_factor=settings.scale,
                            service_workers="block",
                            accept_downloads=False,
                            locale="zh-CN",
                        )

                        async def route_asset(route: Route) -> None:
                            request = route.request
                            url = _public_asset_url(request.url)
                            cached = self._static_images.get(url or "")
                            if (
                                request.resource_type == "image"
                                and cached
                                and url in literal_image_urls
                            ):
                                await route.fulfill(content_type=cached[0], body=cached[1])
                                return
                            if request.resource_type != "image" or url not in allowed_urls:
                                await route.abort()
                                return
                            await route.continue_()

                        await context.route("**/*", route_asset)
                        await context.route_web_socket("**/*", lambda websocket: websocket.close())
                        page = await context.new_page()
                        page.set_default_timeout(5000)
                        page.set_default_navigation_timeout(5000)
                        guard = (
                            '<meta http-equiv="Content-Security-Policy" content="'
                            + html.escape(_CSP, quote=True)
                            + '"><style>'
                            + _LOCAL_FONTS
                            + "</style>"
                        )
                        secured = re.sub(
                            r"(<head\b[^>]*>)",
                            lambda match: match[0] + guard,
                            content,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                        if secured == content:
                            secured = guard + content
                        await page.set_content(secured, wait_until="domcontentloaded")
                        # 不使用 networkidle，失联图片与字体不会拖住整张日报。
                        await page.evaluate("""async () => {
                            await Promise.race([
                                Promise.all([
                                    document.fonts.ready,
                                    ...Array.from(document.images, image => image.complete
                                        ? Promise.resolve()
                                        : new Promise(resolve => {
                                            image.addEventListener('load', resolve, {once:true});
                                            image.addEventListener('error', resolve, {once:true});
                                        }))
                                ]),
                                new Promise(resolve => setTimeout(resolve, 2500))
                            ]);
                            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                        }""")
                        await page.wait_for_timeout(350)
                        dimensions = await page.evaluate("""() => ({
                            width: Math.max(document.documentElement.scrollWidth, innerWidth),
                            height: Math.max(document.documentElement.scrollHeight, innerHeight)
                        })""")
                        width = int(dimensions["width"]) if settings.full_page else settings.width
                        height = (
                            int(dimensions["height"]) if settings.full_page else settings.height
                        )
                        scale = _screenshot_scale(width, height, settings.scale)
                        if settings.image_type == "jpeg":
                            return await page.screenshot(
                                type="jpeg",
                                quality=settings.quality,
                                full_page=settings.full_page,
                                scale=scale,
                                animations="disabled",
                                timeout=10000,
                            )
                        return await page.screenshot(
                            type="png",
                            full_page=settings.full_page,
                            scale=scale,
                            animations="disabled",
                            timeout=10000,
                        )
                    finally:
                        await asyncio.wait_for(browser.close(), timeout=5)

    async def render_to_file(
        self, content: str, options: Mapping[str, object] | None, output_dir: Path
    ) -> str:
        """保存图片供 AstrBot 发送，并限制本地专用缓存文件数量。

        Args:
            content: 已装配的日报 HTML。
            options: AstrBot 图片渲染选项。
            output_dir: 仅供本地日报渲染使用的缓存目录。

        Returns:
            已完整写入的图片绝对路径。
        """
        image = await self.render(content, options)

        def save() -> str:
            output_dir.mkdir(parents=True, exist_ok=True)
            suffix = ".png" if image.startswith(b"\x89PNG") else ".jpg"
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix="local-report-", suffix=suffix, dir=output_dir, delete=False
            ) as target:
                target.write(image)
                result = Path(target.name).resolve()
            cached = sorted(
                output_dir.glob("local-report-*"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for stale in cached[32:]:
                if stale.is_file() and stale.suffix in {".png", ".jpg"}:
                    stale.unlink(missing_ok=True)
            return str(result)

        return await asyncio.to_thread(save)


report_renderer = LocalReportRenderer()
