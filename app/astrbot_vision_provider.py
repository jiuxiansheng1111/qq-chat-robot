"""QQ-only image caption provider with bounded free-model failover and readable long images."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import time

from PIL import Image, ImageOps

PROVIDER_TYPE = "qqchat_resilient_vision"
PROVIDER_ID = "qqchat-qq-vision"
PRIMARY_MODEL = "glm-4.6v-flash"
BACKUP_MODEL = "glm-4.1v-thinking-flash"
_LOGGER = logging.getLogger(__name__)


def _data_url(image):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def readable_image_parts(data):
    """Keep an overview and overlapping detail strips for long screenshots."""
    with Image.open(io.BytesIO(data)) as source:
        animated = getattr(source, "is_animated", False)
        frame = ImageOps.exif_transpose(source).convert("RGBA")
        background = Image.new("RGBA", frame.size, "white")
        background.alpha_composite(frame)
        frame = background.convert("RGB")
    width, height = frame.size
    overview = frame.copy()
    overview.thumbnail((1440, 1440), Image.Resampling.LANCZOS)
    parts = [(_data_url(overview), "全图概览" + ("（动态图只取首帧）" if animated else ""))]
    if max(width, height) / min(width, height) < 2.2:
        return parts
    scale = min(1, 1152 / min(width, height))
    frame = frame.resize((round(width * scale), round(height * scale)), Image.Resampling.LANCZOS)
    vertical = height >= width
    extent = frame.height if vertical else frame.width
    # At most eight strips, with coverage stated explicitly if the image is longer.
    starts = list(range(0, extent, 1344))[:8]
    for start in starts:
        end = min(start + 1440, extent)
        box = (0, start, frame.width, end) if vertical else (start, 0, end, frame.height)
        region = f"{'纵向' if vertical else '横向'} {round(start / extent * 100)}%–{round(end / extent * 100)}%"
        parts.append((_data_url(frame.crop(box)), region))
        if end == extent:
            break
    if starts[-1] + 1440 < extent:
        parts[-1] = (parts[-1][0], parts[-1][1] + "；其后的细节未提供，不能声称已逐字读完")
    return parts


async def prepare_caption_images(refs):
    from astrbot.core.utils.media_utils import resolve_image_ref_to_base64_data

    images, labels = [], []
    refs = list(dict.fromkeys(refs or []))
    for index, ref in enumerate(refs):
        if len(images) >= 16:
            labels.append(f"原图 {index + 1} 及之后的图片未读取")
            break
        resolved = await resolve_image_ref_to_base64_data(ref, strict=True)
        if resolved is None:
            raise ValueError("本轮图片无法读取")
        parts = await asyncio.to_thread(readable_image_parts, resolved.to_bytes())
        for image, label in parts:
            if len(images) >= 16:
                labels.append(f"原图 {index + 1} 后续细节未读取")
                break
            images.append(image)
            labels.append(f"输入图 {len(images)} = 原图 {index + 1} 的{label}")
    if not images:
        raise ValueError("缺少实际图片")
    return images, "\n".join(labels)


def register_vision_provider():
    from astrbot.core.provider.register import provider_cls_map, register_provider_adapter
    from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

    existing = provider_cls_map.get(PROVIDER_TYPE)
    if existing is not None:
        if getattr(existing.cls_type, "qqchat_resilient_vision", False):
            return existing.cls_type
        raise RuntimeError("QQ vision provider type is already registered")

    @register_provider_adapter(PROVIDER_TYPE, "QQ 图片理解（免费视觉主备）")
    class QQVisionProvider(ProviderOpenAIOfficial):
        qqchat_resilient_vision = True
        _primary_busy_until = 0.0

        async def text_chat(self, prompt=None, session_id=None, image_urls=None, **kwargs):
            from astrbot.core.exceptions import EmptyModelOutputError
            from openai import APIConnectionError, APIStatusError

            if kwargs.get("func_tool") or kwargs.get("audio_urls"):
                raise ValueError("This provider is for image understanding only")
            images, layout = await prepare_caption_images(image_urls)
            message = await self.assemble_context(
                (prompt or "描述图片中的实际内容")
                + "\n\n【本次图片布局】\n"
                + layout
                + "\n全图与细节来自同一原图，不重复计数。长报告按实际内容保留标题、关键数字、代表性原文和不确定处，长度按信息量决定。",
                images,
            )
            models = [PRIMARY_MODEL, BACKUP_MODEL]
            if time.monotonic() < self._primary_busy_until:
                models = [BACKUP_MODEL]
            error = None
            for model in models:
                try:
                    # Avoid three layers of automatic retries against an overloaded model.
                    async with asyncio.timeout(35):
                        response = await self.client.with_options(
                            max_retries=0, timeout=32
                        ).chat.completions.create(
                            model=model,
                            messages=[message],
                            stream=False,
                            temperature=0.1,
                            max_tokens=4096,
                            extra_body={"thinking": {"type": "disabled"}},
                        )
                    result = await self._parse_openai_completion(response, None)
                    if not result.completion_text.strip():
                        raise ValueError("视觉服务返回了空内容")
                    _LOGGER.info(
                        "QQ图片识别成功 model=%s images=%s chars=%s",
                        model,
                        len(images),
                        len(result.completion_text),
                    )
                    return result
                except (
                    APIConnectionError,
                    APIStatusError,
                    TimeoutError,
                    ValueError,
                    EmptyModelOutputError,
                ) as exc:
                    status = getattr(exc, "status_code", None)
                    # Authentication, content rejection and malformed input must not be
                    # replayed as capacity failures against another model.
                    if isinstance(exc, APIStatusError) and status not in {
                        408,
                        429,
                        500,
                        502,
                        503,
                        504,
                    }:
                        raise
                    error = exc
                    if model == PRIMARY_MODEL:
                        self._primary_busy_until = time.monotonic() + 120
                    _LOGGER.warning(
                        "QQ图片识别本次未完成 model=%s error=%s status=%s",
                        model,
                        type(exc).__name__,
                        status,
                    )
            raise RuntimeError("本轮视觉来源暂时不可用") from error

        async def text_chat_stream(self, *args, **kwargs):
            yield await self.text_chat(*args, **kwargs)

    return QQVisionProvider
