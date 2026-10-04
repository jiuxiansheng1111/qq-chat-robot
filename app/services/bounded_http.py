"""小响应按块读取，避免一次把远程内容全放进内存。"""

from __future__ import annotations

import asyncio
import json
import zlib
from typing import Any

import httpx


class ResponseTooLarge(ValueError):
    """远程内容超过本次读取上限。"""


async def request_bytes(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float = 20,
    **kwargs: Any,
) -> bytes:
    if max_bytes <= 0 or timeout_seconds <= 0:
        raise ValueError("响应大小和超时必须大于零")
    headers = httpx.Headers(kwargs.pop("headers", None))
    headers["Accept-Encoding"] = "gzip, deflate"
    async with asyncio.timeout(timeout_seconds):
        async with client.stream(
            method, url, headers=headers,
            follow_redirects=False, timeout=timeout_seconds, **kwargs,
        ) as response:
            response.raise_for_status()
            try:
                declared_size = int(response.headers.get("content-length", "0"))
            except ValueError:
                declared_size = 0
            if declared_size > max_bytes:
                raise ResponseTooLarge("远程内容过大，已停止读取")
            encoding = response.headers.get("content-encoding", "identity").strip().lower()
            if encoding not in {"", "identity", "gzip", "deflate"}:
                raise ValueError("服务返回了不支持的压缩格式")
            decoder = (
                zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
                if encoding in {"gzip", "deflate"} else None
            )
            data = bytearray()
            raw_size = 0
            async for raw_chunk in response.aiter_raw(chunk_size=64 * 1024):
                raw_size += len(raw_chunk)
                if raw_size > max_bytes:
                    raise ResponseTooLarge("远程内容过大，已停止读取")
                try:
                    # 解压也限制输出，避免小压缩包先膨胀成一大块内存。
                    chunk = decoder.decompress(raw_chunk, max_bytes - len(data) + 1) if decoder else raw_chunk
                except zlib.error as exc:
                    raise ValueError("服务返回的压缩内容损坏") from exc
                if len(data) + len(chunk) > max_bytes:
                    raise ResponseTooLarge("远程内容过大，已停止读取")
                data.extend(chunk)
            if decoder is not None and (not decoder.eof or decoder.unused_data):
                raise ValueError("服务返回的压缩内容不完整")
            return bytes(data)


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_bytes: int = 512 * 1024,
    timeout_seconds: float = 20,
    **kwargs: Any,
) -> Any:
    data = await request_bytes(
        client, method, url, max_bytes=max_bytes,
        timeout_seconds=timeout_seconds, **kwargs,
    )
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("服务返回的内容格式不正确") from exc
