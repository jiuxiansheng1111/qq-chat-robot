"""将三张真实剧情彩图排成连环漫画，中文只在本地排版。"""

from __future__ import annotations

import asyncio
import base64
import html
import io
import re
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from app.services.local_report_render import report_renderer

_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
_HEIGHT = 620
_ART_HEIGHT = 304


def _short(value: str, limit: int) -> str:
    """缩短本地文字并隐藏号码，不向图中插入可执行标记。"""
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    value = re.sub(r"(?<!\d)\d{5,13}(?!\d)", "群友", value)
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _panel_image(payload: bytes) -> tuple[str, float]:
    """确认输入是完整的静态彩图，并保留原始字节和宽高比。"""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= 4 * 1024 * 1024:
        raise ValueError("单格漫画图片为空、格式无效或超过4MiB")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            mime = _MIMES.get(image.format or "")
            width, height = image.size
            if (
                not mime
                or min(width, height) < 120
                or max(width, height) > 8192
                or width * height > 16_000_000
                or getattr(image, "is_animated", False)
            ):
                raise ValueError("单格漫画必须是尺寸合理的静态PNG、JPEG或WebP")
            if image.getexif().get(274) in {5, 6, 7, 8}:
                width, height = height, width
            image.load()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("单格漫画图片损坏或无法读取") from exc
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii"), width / height


def _layout_html(
    panels: Sequence[bytes], captions: Sequence[str], title: str, source_note: str
) -> tuple[str, int]:
    if (
        isinstance(panels, (str, bytes, bytearray))
        or not isinstance(panels, Sequence)
        or len(panels) != 3
        or isinstance(captions, (str, bytes))
        or not isinstance(captions, Sequence)
        or len(captions) != 3
        or any(not isinstance(caption, str) for caption in captions)
    ):
        raise ValueError("连环漫画需要恰好三张彩图与三条短说明")
    if sum(len(panel) for panel in panels if isinstance(panel, bytes)) > 8 * 1024 * 1024:
        raise ValueError("三格漫画图片总量超过8MiB")
    images = [_panel_image(panel) for panel in panels]
    width = 1000
    frames = []
    for index, ((uri, _ratio), caption) in enumerate(zip(images, captions, strict=True), 1):
        frames.append(
            f'<figure><div class="art"><img src="{uri}" alt="第{index}格剧情彩图"></div>'
            f"<figcaption><span>{index:02}</span><p>{html.escape(_short(caption, 36))}</p>"
            "</figcaption></figure>"
        )
    title = html.escape(_short(title, 22))
    note = html.escape(_short(source_note, 40))
    content = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
        <meta name="viewport" content="width={width}, height={_HEIGHT}"><title>{title}</title>
        <style>*{{box-sizing:border-box}}html,body{{margin:0;width:{width}px;height:{_HEIGHT}px;overflow:hidden}}
        body{{background:#f6f0e7;color:#25333b;font-family:'Microsoft YaHei','Noto Sans SC',sans-serif}}
        .page{{padding:20px}}header{{height:88px}}.label{{font-size:18px;color:#7f7166;line-height:24px}}
        h1{{font-size:32px;line-height:1.2;margin:5px 0 0;font-weight:800;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;overflow-wrap:anywhere}}
        main{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}figure{{margin:0;height:420px;background:#fffdf9;border:2px solid #4b4b49;border-radius:12px;overflow:hidden}}
        .art{{height:{_ART_HEIGHT}px;width:100%;background:#eae6df;display:flex;align-items:center;justify-content:center}}
        img{{display:block;width:100%;height:100%;object-fit:contain;image-orientation:from-image}}
        figcaption{{height:112px;padding:8px 10px;display:flex;align-items:center;gap:8px;border-top:1px solid #ddd4c8}}
        figcaption span{{font-size:18px;font-weight:800;color:#9a6d4f;flex:0 0 24px}}
        p{{margin:0;font-size:22px;line-height:1.35;overflow-wrap:anywhere;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden}}
        footer{{height:70px;padding-top:7px}}.disclaimer{{font-size:19px;line-height:24px;font-weight:700}}
        .source{{font-size:18px;line-height:22px;color:#716b64;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;overflow-wrap:anywhere}}
        </style></head><body><div class="page"><header><div class="label">三格连环漫画</div><h1>{title}</h1></header>
        <main>{"".join(frames)}</main><footer><div class="disclaimer">据群聊话题改编 · 剧情为创作</div>
        <div class="source">{note}</div></footer></div></body></html>"""
    return content, width


async def render_group_comic(
    panels: Sequence[bytes],
    captions: Sequence[str],
    output_dir: Path,
    *,
    title: str,
    source_note: str,
) -> bytes:
    """等比完整呈现三张剧情彩图，返回带清晰中文旁白的PNG。

    Args:
        panels: 按同一话题剧情顺序排列的三张真实生成彩图字节。
        captions: 每格简短旁白，保持剧情顺序。
        output_dir: 本地渲染临时目录。
        title: 漫画标题。
        source_note: 真实话题来源的简短说明。

    Returns:
        1000×620的紧凑三格连环漫画PNG。

    Raises:
        ValueError: 图片数量、格式、尺寸或说明不符合要求。
    """
    content, width = await asyncio.to_thread(_layout_html, panels, captions, title, source_note)
    destination = await report_renderer.render_to_file(
        content,
        {
            "type": "png",
            "viewport_width": width,
            "viewport_height": _HEIGHT,
            "device_scale_factor": 1,
            "full_page": False,
        },
        Path(output_dir),
    )
    return await asyncio.to_thread(Path(destination).read_bytes)
