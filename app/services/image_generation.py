"""Opt-in image generation adapter for the QQ bot.

The adapter intentionally speaks the small, common OpenAI-compatible images
API surface. It accepts either ``data[].b64_json`` or ``data[].url`` and
normalizes both to a OneBot ``base64://`` image payload. Providers remain
configurable so the bot does not depend on a single vendor.
"""

import base64
import ipaddress
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlsplit

import httpx
from PIL import Image

from app.config import Settings


@dataclass(frozen=True)
class GeneratedImage:
    data: str
    provider: str
    image_url: str = ""


def _endpoint(settings: Settings) -> str:
    value = str(settings.image_generation_api_url or "").strip().rstrip("/")
    if not value:
        raise RuntimeError("图片生成功能还没有配置 IMAGE_GENERATION_API_URL")
    if not value.endswith("/images/generations"):
        value += "/images/generations"
    return value


def _validate_and_encode(raw: bytes, settings: Settings) -> str:
    if not raw or len(raw) > settings.media_max_bytes:
        raise RuntimeError("图片生成结果为空或超过 MEDIA_MAX_BYTES")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.width < 64 or image.height < 64:
                raise ValueError("图片尺寸过小")
            image.verify()
    except Exception as exc:
        raise RuntimeError("图片生成接口返回的内容不是有效图片") from exc
    return "base64://" + base64.b64encode(raw).decode("ascii")


def _safe_remote_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("图片生成接口返回了不安全的图片地址")
    host = parsed.hostname.strip("[]").lower()
    if host in {"localhost", "metadata.google.internal", "host.docker.internal"}:
        raise RuntimeError("图片生成结果不能指向本机或云元数据地址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
        raise RuntimeError("图片生成结果不能指向内网地址")
    return value


async def _download_limited_image(client: httpx.AsyncClient, url: str, settings: Settings) -> bytes:
    url = _safe_remote_url(url)
    async with client.stream("GET", url, follow_redirects=False) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not content_type.startswith("image/"):
            raise RuntimeError("图片生成接口的 URL 没有返回图片内容")
        declared_size = int(response.headers.get("content-length") or 0)
        if declared_size > settings.media_max_bytes:
            raise RuntimeError("图片生成结果超过 MEDIA_MAX_BYTES")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > settings.media_max_bytes:
                raise RuntimeError("图片生成结果超过 MEDIA_MAX_BYTES")
    return bytes(content)


async def generate_image(prompt: str, settings: Settings) -> GeneratedImage:
    if not settings.image_generation_enabled:
        raise RuntimeError("图片生成功能未开启，请设置 IMAGE_GENERATION_ENABLED=true")
    prompt = " ".join(str(prompt or "").split())
    if not prompt:
        raise ValueError("请告诉我想生成什么图片")
    if len(prompt) > max(50, int(settings.image_generation_max_prompt_chars)):
        raise ValueError(
            f"图片描述太长了，请控制在 {settings.image_generation_max_prompt_chars} 字以内"
        )
    endpoint = _endpoint(settings)
    headers = {"Content-Type": "application/json"}
    if settings.image_generation_api_key:
        headers["Authorization"] = f"Bearer {settings.image_generation_api_key}"
    payload = {
        "model": settings.image_generation_model,
        "prompt": prompt,
        "n": 1,
        "size": settings.image_generation_size,
        "response_format": "b64_json",
    }
    if not payload["model"]:
        payload.pop("model")
    timeout = min(max(float(settings.image_generation_timeout_seconds), 5), 180)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        items = body.get("data") if isinstance(body, dict) else None
        if not isinstance(items, list) or not items:
            raise RuntimeError("图片生成接口没有返回 data 图片结果")
        item = items[0] if isinstance(items[0], dict) else {}
        encoded = item.get("b64_json") or item.get("base64")
        if encoded:
            try:
                raw = base64.b64decode(str(encoded), validate=True)
            except (ValueError, TypeError) as exc:
                raise RuntimeError("图片生成接口返回的 base64 无法解码") from exc
            return GeneratedImage(_validate_and_encode(raw, settings), "image-generation")
        image_url = str(item.get("url") or "").strip()
        if not image_url:
            raise RuntimeError("图片生成接口既没有 b64_json，也没有 url")
        raw = await _download_limited_image(client, image_url, settings)
        return GeneratedImage(
            _validate_and_encode(raw, settings),
            "image-generation",
            image_url=image_url,
        )
