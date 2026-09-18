import asyncio
import base64
import random
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
_pig_title_pool: list[str] = []
_pig_pool_lock = asyncio.Lock()


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
    if api_key:
        # TheCatAPI uses x-api-key; other JSON image providers commonly use Bearer.
        hostname = (urlparse(url).hostname or "").lower()
        if hostname.endswith("thecatapi.com"):
            headers["x-api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
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
    return "base64://" + base64.b64encode(response.content).decode()
