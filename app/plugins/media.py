import asyncio
import base64
import hashlib
import random
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.config import Settings

WIKIMEDIA_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIMEDIA_USER_AGENT = "qq-chatrobot/0.1 (https://github.com/jiuxiansheng1111/qq-chat-robot)"
REAL_PIG_TITLES = (
    "File:EC Black Piglets.JPG",
    "File:Landschwein5.jpg",
    "File:Feeding the pigs.JPG",
    "File:GZ Sunflowers Garden 百萬葵園 Piggy.jpg",
    "File:Black piglets at Barlow, North Yorkshire, England - geograph.org.uk - 420819.jpg",
    "File:Gloucester Old Spot Piglets.jpg",
    "File:An inquisitive piglet at Treddolphin Farm - geograph.org.uk - 1067576.jpg",
    "File:Freches Ferkel German Piétrain.JPG",
    "File:Ferkel Mangalitza-Wollschwein.JPG",
    "File:Ferkel nasse Schnauze.JPG",
    "File:Ferkel an der Schnauze des Ebers.JPG",
    "File:Herzogenriedpark MA Ferkel Wollschwein.JPG",
    "File:Eine Runde Schlafen Mangalitza-Ferkel.JPG",
    "File:Kuschelnde Ferkel Wollschweine.JPG",
    "File:Einmal strecken buntes Bentheimer Landschwein Ferkel 2011.JPG",
    "File:Cute Piglet.jpg",
    "File:Baby piglets.jpg",
    "File:Little pigs.jpg",
    "File:Cochinitos jugando - Piglets playing (392774544).jpg",
    "File:Chvatěruby, černé selátko.jpg",
    "File:Kune Kune Piglets (3716701922).jpg",
    "File:Ferkel im Stroh auf Neuland Hof.jpg",
    "File:HSC-piglet-closeup (6882378813).jpg",
    "File:HSC-piglet-closeup (6916038449).jpg",
    "File:A piglet at Orchid Island 20100914.jpg",
    "File:Baby-Bauernhoftiere 002 2014 03 16.jpg",
    "File:Contre-jour photograph of a standing piglet at sunset with colorful sky in Don Det Laos.jpg",
)
NAILONG_SOURCE_URL = "https://github.com/GGGeeeooorrrgggeee/nailong-memes"
NAILONG_RAW_BASE_URL = (
    "https://raw.githubusercontent.com/GGGeeeooorrrgggeee/nailong-memes/main/"
)
NAILONG_PATHS = (
    "images/8eca876e50b8fcaed66f3301158085ea.jpg",
    "images/daeaf989a265314fd5f4ad59a15c4477.jpg",
    "images/cc7e81700e377f5ae9266d07cca6af9c.jpg",
    "images/DF6C6ABBE88D0DE497EB8F3D48190D1A.jpg",
    "images/274d629bfa4fc646138fe36ea27f3f35.jpg",
    "images/522b5f888cffd66abd8bd705e6598bad.jpg",
    "images/2EC91AB5920CA4C4DA480F5EFD0DA10E.jpg",
    "images/367aaf3f9b80de98d1da27597d9156fe.jpg",
    "images/84a5c63b0b8119a0651dcba6f649093e.jpg",
    "images/1a3dc8c7531025275e8625ba9b6e38cc.jpg",
    "images/B25A9251B83C7B06077223A9D4A546D0.jpg",
    "images/f55bba1656311e56ee5ca10263a19c67.jpg",
    "images/706DF161165F0563317CFE4122C88A3D.jpg",
    "images/0e8bf92c86516402967aa5200ccec00e.jpg",
    "images/CF0E9EB8A8B36AC7C06F03F2B955794A.jpg",
    "images/925F19D8F5DBBF3C30074373248C48E7.jpg",
    "images/6a50b8284930bd1a281e1579acb0d6d9.jpg",
    "images/E314D78C748F0646029B5D69AFEC59B9.jpg",
    "images/bff75a7ca42493021f1c0bcfc3792748.jpg",
    "images/f4a92c91d3291c98476a6cf297b5d09d.jpg",
    "gif/6F91055B6FFD248B37F53F514E4AEA84.gif",
    "gif/5AE5E51882D1B81F043948457759AEDE.gif",
    "gif/FFFC2EAC8A8DA120ABC5DFC49BE9F8AB.gif",
    "gif/CD6514E927142183B92C75CB0EAF6F63.gif",
    "gif/9337EA0772D5F796F3D2353D02572405.gif",
    "gif/E35FDFD1E4DEA66B0F1798EC8EC0F3AC.gif",
    "gif/C165854113012560E7979E3A4AF597AF.gif",
    "gif/242B39C1E5E91DB01ED6285F86D2D5FD.gif",
    "gif/D046006C8E6A57F1C4B8FB044F387401.gif",
    "gif/C818B7AB164236821ADFEF7D49517AE5.gif",
    "gif/A98366210683ACF2FE59959D4E63F43A.gif",
    "gif/8114153C1B2AE768A5433C2002E97DFF.gif",
    "gif/D7BB82FDCEFE4304DEFC2DC1CC132655.gif",
    "gif/79F6C69BFA5CBD8AE41EDE97346290E9.gif",
    "gif/74B04107B41A03251A17952FB7E3B466.gif",
    "gif/A320A619BA1884924EC8377E56DF9280.gif",
    "gif/71808EA8C5342D504532E0BE5F57F8C7.gif",
    "gif/FDB82F7AF37B14CC7D21EB1FFBF7F9B6.gif",
    "gif/E21219FE5409935F94D2BFA8B43E0726.gif",
    "gif/E3C08F67A9F633C707E6B922EAA038B5.gif",
)
_pig_title_pool: list[str] = []
_pig_pool_lock = asyncio.Lock()
_nailong_path_pool: list[str] = []
_nailong_pool_lock = asyncio.Lock()
_cat_gif_cache: deque[str] = deque()
_cat_cached_hashes: set[str] = set()
_cat_recent_hashes: deque[str] = deque(maxlen=16)
_cat_cache_lock = asyncio.Lock()
_cat_fill_lock = asyncio.Lock()


def _cat_digest(image: str) -> str:
    return hashlib.sha256(image.encode("ascii", errors="ignore")).hexdigest()


def _remember_cat_digest(digest: str) -> None:
    if digest in _cat_recent_hashes:
        try:
            _cat_recent_hashes.remove(digest)
        except ValueError:
            pass
    _cat_recent_hashes.append(digest)


@dataclass(frozen=True)
class RealPigImage:
    url: str
    source_url: str


async def _next_pig_title() -> str:
    async with _pig_pool_lock:
        if not _pig_title_pool:
            _pig_title_pool.extend(REAL_PIG_TITLES)
            random.SystemRandom().shuffle(_pig_title_pool)
        return _pig_title_pool.pop()


async def _next_nailong_path() -> str:
    async with _nailong_pool_lock:
        if not _nailong_path_pool:
            _nailong_path_pool.extend(NAILONG_PATHS)
            random.SystemRandom().shuffle(_nailong_path_pool)
        return _nailong_path_pool.pop()


async def _download_cat_gif_once(settings: Settings) -> str:
    url = httpx.URL(settings.cat_api_url).copy_merge_params(
        {"width": "480", "height": "480", "_": str(time.time_ns())}
    )
    timeout = min(float(getattr(settings, "cat_timeout_seconds", 12)), 20)
    max_bytes = settings.media_max_bytes
    headers = {"Cache-Control": "no-cache", "User-Agent": WIKIMEDIA_USER_AGENT}
    content = bytearray()
    async with (
        httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        if response.status_code >= 400:
            raise RuntimeError(f"猫图服务错误: {response.status_code}")
        if not response.headers.get("content-type", "").startswith("image/"):
            raise RuntimeError("猫图服务返回的内容不是图片")
        declared_size = int(response.headers.get("content-length") or 0)
        if declared_size > max_bytes:
            raise RuntimeError("猫 GIF 超过大小限制")
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > max_bytes:
                raise RuntimeError("猫 GIF 超过大小限制")
    if not bytes(content).startswith((b"GIF87a", b"GIF89a")):
        raise RuntimeError("猫图服务未返回 GIF")
    return "base64://" + base64.b64encode(content).decode()


async def _download_cat_gif(settings: Settings) -> str:
    attempts = max(1, min(int(getattr(settings, "media_retry_attempts", 2)), 4))
    last_error: RuntimeError | httpx.HTTPError | None = None
    for attempt in range(attempts):
        try:
            return await _download_cat_gif_once(settings)
        except (RuntimeError, httpx.HTTPError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(0.4 * (attempt + 1))
    raise last_error or RuntimeError("猫图下载失败")


async def warm_cat_gif_cache(settings: Settings) -> None:
    target = max(1, min(int(getattr(settings, "cat_cache_size", 6)), 10))
    async with _cat_fill_lock:
        attempts_left = max(8, target * 4)
        while attempts_left > 0:
            async with _cat_cache_lock:
                if len(_cat_gif_cache) >= target:
                    return
            attempts_left -= 1
            try:
                image = await _download_cat_gif(settings)
            except (RuntimeError, httpx.HTTPError):
                return
            digest = _cat_digest(image)
            async with _cat_cache_lock:
                if digest in _cat_cached_hashes or digest in _cat_recent_hashes:
                    continue
                _cat_gif_cache.append(image)
                _cat_cached_hashes.add(digest)


async def maintain_cat_gif_cache(settings: Settings) -> None:
    """Keep refilling the cat cache after transient upstream failures."""
    target = max(1, min(int(getattr(settings, "cat_cache_size", 6)), 10))
    while True:
        await warm_cat_gif_cache(settings)
        async with _cat_cache_lock:
            ready = len(_cat_gif_cache) >= target
        await asyncio.sleep(20 if ready else 3)


async def random_cat_gif(settings: Settings) -> str:
    async with _cat_cache_lock:
        image = _cat_gif_cache.popleft() if _cat_gif_cache else None
        if image is not None:
            digest = _cat_digest(image)
            _cat_cached_hashes.discard(digest)
            _remember_cat_digest(digest)

    if image is None:
        # CATAAS can occasionally return the same GIF repeatedly even with a
        # cache-busting query. Compare actual content and retry a few times.
        last_image = ""
        for _ in range(8):
            candidate = await _download_cat_gif(settings)
            last_image = candidate
            digest = _cat_digest(candidate)
            async with _cat_cache_lock:
                if digest in _cat_recent_hashes:
                    continue
                _remember_cat_digest(digest)
            image = candidate
            break
        if image is None:
            image = last_image or await _download_cat_gif(settings)

    asyncio.create_task(warm_cat_gif_cache(settings))
    return image


async def random_nailong_image(settings: Settings) -> str:
    """Download one non-repeating image from the curated open-source meme pool."""
    path = await _next_nailong_path()
    url = NAILONG_RAW_BASE_URL + path
    timeout = getattr(settings, "media_timeout_seconds", 60)
    attempts = max(1, getattr(settings, "media_retry_attempts", 2))
    headers = {"User-Agent": WIKIMEDIA_USER_AGENT}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(attempts):
            try:
                response = await client.get(url, headers=headers)
                break
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(0.5)

    content_type = response.headers.get("content-type", "")
    if response.status_code >= 400:
        raise RuntimeError(f"奶龙图库下载错误: {response.status_code}")
    if not content_type.startswith("image/"):
        raise RuntimeError("奶龙图库返回的内容不是图片")
    if len(response.content) > settings.media_max_bytes:
        raise RuntimeError("奶龙图片超过大小限制")
    return "base64://" + base64.b64encode(response.content).decode()


async def random_real_pig_image(api_url: str, settings: Settings) -> RealPigImage:
    """Return a non-repeating real pig photograph from a curated Commons pool."""
    endpoint = (
        api_url
        if (urlparse(api_url).hostname or "").endswith("wikimedia.org")
        else WIKIMEDIA_COMMONS_API
    )
    params = {
        "action": "query",
        "titles": await _next_pig_title(),
        "redirects": "1",
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": "1024",
        "format": "json",
        "formatversion": "2",
    }
    timeout = getattr(settings, "media_timeout_seconds", 60)
    attempts = max(1, getattr(settings, "media_retry_attempts", 2))
    headers = {"User-Agent": WIKIMEDIA_USER_AGENT}

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(attempts):
            try:
                response = await client.get(endpoint, params=params, headers=headers)
                break
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(0.5)

        if response.status_code >= 400:
            raise RuntimeError(f"Wikimedia Commons API 错误: {response.status_code}")
        pages = response.json().get("query", {}).get("pages", [])
        image_info = pages[0].get("imageinfo", [{}])[0] if pages else {}
        image_url = image_info.get("thumburl") or image_info.get("url")
        source_url = image_info.get("descriptionurl", "https://commons.wikimedia.org/")
        if not isinstance(image_url, str) or not image_url.startswith("https://"):
            raise RuntimeError("Wikimedia Commons 未返回可用的真实猪图")

        for attempt in range(attempts):
            try:
                image_response = await client.get(image_url, headers=headers)
                break
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(0.5)

    content_type = image_response.headers.get("content-type", "")
    if image_response.status_code >= 400:
        raise RuntimeError(f"Wikimedia Commons 图片下载错误: {image_response.status_code}")
    if not content_type.startswith("image/"):
        raise RuntimeError("Wikimedia Commons 返回的内容不是图片")
    if len(image_response.content) > settings.media_max_bytes:
        raise RuntimeError("Wikimedia Commons 图片超过大小限制")
    encoded = base64.b64encode(image_response.content).decode()
    return RealPigImage(f"base64://{encoded}", source_url)


async def random_image(url: str, api_key: str, settings: Settings) -> str:
    if not url:
        raise RuntimeError("图片 API 未配置")
    headers = {}
    hostname = (urlparse(url).hostname or "").lower()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if hostname.endswith("cataas.com"):
        headers["Cache-Control"] = "no-cache"
    timeout = getattr(settings, "media_timeout_seconds", 60)
    attempts = max(1, getattr(settings, "media_retry_attempts", 2))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for attempt in range(attempts):
            try:
                response = await client.get(url, headers=headers)
                break
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(0.5)
    if response.status_code >= 400:
        raise RuntimeError(f"图片 API 错误: {response.status_code}")
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            data = response.json()
            image_url = None
            if isinstance(data, list) and data and isinstance(data[0], dict):
                image_url = data[0].get("url") or data[0].get("image_url")
            elif isinstance(data, dict):
                nested = data.get("data")
                if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                    image_url = nested[0].get("url") or nested[0].get("image_url")
                elif isinstance(nested, dict):
                    image_url = nested.get("url") or nested.get("image_url")
                image_url = image_url or data.get("image_url") or data.get("url")
            if image_url and isinstance(image_url, str) and image_url.startswith("https://"):
                return image_url
        except (ValueError, AttributeError):
            pass
        raise RuntimeError("图片 API 未返回可用 image_url")
    if not content_type.startswith("image/") or len(response.content) > settings.media_max_bytes:
        raise RuntimeError("图片格式或大小不符合要求")
    if hostname.endswith("cataas.com") and not response.content.startswith((b"GIF87a", b"GIF89a")):
        raise RuntimeError("猫图服务未返回 GIF")
    return "base64://" + base64.b64encode(response.content).decode()
