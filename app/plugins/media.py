import asyncio
import base64
import secrets
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import httpx

from app.config import Settings


def randomized_provider_url(url: str) -> str:
    """Give Pollinations requests a unique seed so responses are not cached clones."""
    hostname = (urlparse(url).hostname or "").lower()
    if not hostname.endswith("pollinations.ai"):
        return url

    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() != "seed"
    ]
    query.append(("seed", str(secrets.randbelow(2_147_483_648))))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query, safe="/"), parts.fragment)
    )


async def random_image(url: str, api_key: str, settings: Settings) -> str:
    if not url:
        raise RuntimeError("图片 API 未配置")
    headers = {}
    if api_key:
        # TheCatAPI uses x-api-key, while most OpenAI-compatible image
        # services (including Pollinations) use Bearer authentication.
        hostname = (urlparse(url).hostname or "").lower()
        if hostname.endswith("thecatapi.com"):
            headers["x-api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    timeout = getattr(settings, "media_timeout_seconds", 60)
    attempts = max(1, getattr(settings, "media_retry_attempts", 2))
    request_url = randomized_provider_url(url)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for attempt in range(attempts):
            try:
                response = await client.get(request_url, headers=headers)
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
