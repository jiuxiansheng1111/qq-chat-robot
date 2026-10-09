"""从 GIPHY 猫图页面选动态 GIF，不落盘缓存媒体。"""

import asyncio
import base64
import html
import io
import json
import random
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx
from PIL import Image

from app.services.cat_history import (
    CatHistoryError,
    CatImageHistory,
    NoNewCatImage,
    fingerprint_cat_gif,
)
from app.services.http_routing import outbound_http_client

GIPHY_CAT_PAGE = "https://giphy.com/gifs/art-cat-HMDsITZh2SBGM"
_PAGE_CANDIDATES: dict[str, tuple["GiphyGif", ...]] = {}
_IN_FLIGHT_IDS: set[str] = set()
_RECENT_LOCK = asyncio.Lock()
_NEXT_CHUNKS = re.compile(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)')
_CAT_WORDS = re.compile(r"\b(?:cats?|kitten|kitty|meow|feline)\b", re.IGNORECASE)


@dataclass(frozen=True)
class GiphyGif:
    id: str
    url: str


def _gif_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = html.unescape(value)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or not host.endswith(".giphy.com")
            or parsed.username or parsed.password or port not in (None, 443)):
        return None
    if not parsed.path.lower().endswith(".gif") or parsed.path.lower().endswith("_s.gif"):
        return None
    return value


class _GiphyMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if (tag == "meta" and values.get("property") == "og:image"
                and (url := _gif_url(values.get("content")))):
            self.urls.append(url)


def giphy_cat_candidates(page: str, page_url: str, max_bytes: int) -> list[GiphyGif]:
    """读取页面里的 GIF 元数据；网页、视频和静态预览都不发送。"""
    seed_id = urlsplit(page_url).path.rsplit("-", 1)[-1]
    found: dict[str, GiphyGif] = {}

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            identity = str(value.get("id") or "")
            images = value.get("images")
            title = str(value.get("title") or "") + " " + str(value.get("alt_text") or "")
            if (identity and value.get("type") == "gif" and isinstance(images, dict)
                    and (identity == seed_id or _CAT_WORDS.search(title))):
                original = images.get("original") or {}
                frames = str(original.get("frames") or "")
                if not frames.isdigit() or int(frames) > 1:
                    for name in ("original", "downsized_medium", "downsized", "fixed_height"):
                        rendition = images.get(name)
                        if not isinstance(rendition, dict):
                            continue
                        size = str(rendition.get("size") or "")
                        url = _gif_url(rendition.get("url"))
                        if url and (not size.isdigit() or int(size) <= max_bytes):
                            found[identity] = GiphyGif(identity, url)
                            break
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    chunks = []
    for match in _NEXT_CHUNKS.finditer(page):
        try:
            chunks.append(json.loads(match.group(1)))
        except ValueError:
            continue
    for line in "".join(chunks).splitlines():
        try:
            payload, _ = json.JSONDecoder().raw_decode(line.partition(":")[2])
        except ValueError:
            continue
        visit(payload)
    meta = _GiphyMeta()
    meta.feed(page)
    if seed_id not in found and meta.urls:
        found[seed_id] = GiphyGif(seed_id, meta.urls[0])
    if not found and re.fullmatch(r"[A-Za-z0-9]{4,128}", seed_id):
        found[seed_id] = GiphyGif(seed_id, f"https://media.giphy.com/media/{seed_id}/giphy.gif")
    return list(found.values())


async def _bounded_get(client: httpx.AsyncClient, url: str, maximum: int) -> bytes:
    content = bytearray()
    accept = "image/gif" if urlsplit(url).path.lower().endswith(".gif") else "text/html"
    async with client.stream("GET", url, headers={"Accept": accept}) as response:
        if response.status_code >= 400:
            raise RuntimeError(f"GIPHY 暂时不可用：{response.status_code}")
        try:
            declared = int(response.headers.get("content-length") or 0)
        except ValueError as exc:
            raise RuntimeError("GIPHY 文件大小无效") from exc
        if declared > maximum:
            raise RuntimeError("GIPHY 文件超过大小限制")
        async for piece in response.aiter_bytes():
            content.extend(piece)
            if len(content) > maximum:
                raise RuntimeError("GIPHY 文件超过大小限制")
    return bytes(content)


def _animated_gif(content: bytes) -> bool:
    if not content.startswith((b"GIF87a", b"GIF89a")):
        return False
    try:
        with Image.open(io.BytesIO(content)) as image:
            return image.format == "GIF" and getattr(image, "n_frames", 1) > 1
    except (OSError, ValueError):
        return False


async def random_giphy_cat_gif(settings) -> str:
    history = CatImageHistory.from_settings(settings)
    timeout = min(float(getattr(settings, "cat_timeout_seconds", 12)), 20)
    maximum = int(settings.media_max_bytes)
    page_url = str(getattr(settings, "cat_giphy_page_url", GIPHY_CAT_PAGE))
    host = (urlsplit(page_url).hostname or "").lower()
    if urlsplit(page_url).scheme != "https" or host not in {"giphy.com", "www.giphy.com"}:
        raise RuntimeError("GIPHY 猫图页面地址无效")
    async with (
        asyncio.timeout(timeout + 3),
        outbound_http_client(timeout=timeout, follow_redirects=True, headers={
            # 取完整页面，推荐图也要一起读。
            "Accept": "text/html",
        }) as client,
    ):
        page = await _bounded_get(client, page_url, 2 * 1024 * 1024)
        candidates = giphy_cat_candidates(page.decode("utf-8", errors="replace"), page_url, maximum)
        async with _RECENT_LOCK:
            # 页面偶尔只返回主图，保留看过的链接；图片本身不缓存。
            combined = {gif.id: gif for gif in _PAGE_CANDIDATES.get(page_url, ())}
            combined.update({gif.id: gif for gif in candidates})
            candidates = list(combined.values())[-64:]
            if len(_PAGE_CANDIDATES) >= 4 and page_url not in _PAGE_CANDIDATES:
                _PAGE_CANDIDATES.pop(next(iter(_PAGE_CANDIDATES)))
            _PAGE_CANDIDATES[page_url] = tuple(candidates)
        known = await history.known_sources([f"giphy:{gif.id}" for gif in candidates])
        tried: set[str] = set()
        for _ in range(min(12, len(candidates))):
            async with _RECENT_LOCK:
                available = [gif for gif in candidates
                             if gif.id not in _IN_FLIGHT_IDS and gif.id not in tried
                             and f"giphy:{gif.id}" not in known]
                if not available:
                    break
                gif = random.SystemRandom().choice(available)
                _IN_FLIGHT_IDS.add(gif.id)
                tried.add(gif.id)
            accepted = False
            try:
                content = await _bounded_get(client, gif.url, maximum)
                fingerprint = await asyncio.to_thread(fingerprint_cat_gif, content)
                accepted = await history.claim(fingerprint, f"giphy:{gif.id}")
            except CatHistoryError:
                raise
            except (RuntimeError, httpx.HTTPError):
                pass
            finally:
                async with _RECENT_LOCK:
                    _IN_FLIGHT_IDS.discard(gif.id)
            if accepted:
                # 发已经核验并登记的字节，避免 QQ 再次取 URL 时收到另一份内容。
                return "base64://" + base64.b64encode(content).decode("ascii")
    raise NoNewCatImage("GIPHY 这次没有未发过的动态猫 GIF")
