"""Compare actual QQ image attachments and draw only regions returned by vision."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import re

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat

from app.astrbot_vision_provider import PROVIDER_ID

_LOG = logging.getLogger(__name__)


def image_refs(event):
    """A quoted original comes before images attached to the reply."""
    def refs(parts):
        result = []
        for item in parts:
            kind = getattr(item, "type", "")
            if str(getattr(kind, "value", kind)).lower() == "image":
                ref = item.url or item.file or item.path
                if ref:
                    result.append(ref)
        return result

    chain = getattr(event.message_obj, "message", ()) or ()
    current = refs(chain)
    quoted = [ref for part in chain for ref in refs(getattr(part, "chain", ()) or ())]
    # Keep the native QQ URL when available. Preprocessed local paths can expire
    # between two messages. Use only actual attachment segments, never chat text.
    raw = event.message_obj.raw_message.get("message")
    if isinstance(raw, list):
        segments = [p.get("data") or {} for p in raw if isinstance(p, dict) and p.get("type") == "image"]
        if len(segments) == len(current):
            current = [seg.get("url") or ref for seg, ref in zip(segments, current)]
        elif not current:
            current = [p.get("url") or p["file"] for p in segments if p.get("url") or p.get("file")]
    return quoted, current


def parse_object(text):
    body = str(text or "").strip()
    fence = re.fullmatch(r"\x60{3}(?:json)?\s*(.*?)\s*\x60{3}", body, re.IGNORECASE | re.DOTALL)
    value = json.loads(fence[1] if fence else body)
    if not isinstance(value, dict):
        raise TypeError("expected object")
    return value


async def _load_image(ref):
    from astrbot.core.utils.media_utils import resolve_image_ref_to_base64_data

    resolved = await resolve_image_ref_to_base64_data(ref, strict=True)
    if resolved is None:
        raise ValueError("unreadable image")
    return await asyncio.to_thread(_decode_image, resolved.to_bytes())


def _decode_image(data):
    if len(data) > 28 * 1024 * 1024:
        raise ValueError("oversized image")
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 32_000_000:
            raise ValueError("oversized dimensions")
        animated = bool(getattr(source, "is_animated", False))
        frame = ImageOps.exif_transpose(source).convert("RGBA")
        frame.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        base = Image.new("RGB", frame.size, "white")
        base.paste(frame, mask=frame.getchannel("A"))
    return base, animated


def _box(value):
    if not isinstance(value, list) or len(value) != 4 or not all(
        type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1000 for x in value
    ):
        return None
    x0, y0, x1, y1 = value
    return value if 3 <= x1 - x0 <= 800 and 3 <= y1 - y0 <= 800 else None


def _sheet(images, marks):
    frames = [ImageOps.contain(img, (740, 800), Image.Resampling.LANCZOS) for img in images]
    width, height = max(f.width for f in frames), max(f.height for f in frames)
    sheet = Image.new("RGB", (width * 2 + 36, height + 48), "#ecf0f4")
    draw = ImageDraw.Draw(sheet)
    for col, frame in enumerate(frames):
        x, y = 12 + col * (width + 12) + (width - frame.width) // 2, 36 + (height - frame.height) // 2
        sheet.paste(frame, (x, y))
        draw.text((x, 8), "AB"[col], fill="#182b43", font_size=20)
        for number, boxes in marks:
            box = boxes[col]
            if box is None:
                continue
            left, top, right, bottom = (x + frame.width * box[0] / 1000, y + frame.height * box[1] / 1000,
                                        x + frame.width * box[2] / 1000, y + frame.height * box[3] / 1000)
            draw.ellipse((left, top, right, bottom), outline="white", width=5)
            draw.ellipse((left, top, right, bottom), outline="#c01857", width=3)
            draw.text((left, max(y, top - 20)), str(number), fill="white", stroke_width=2,
                      stroke_fill="#a50949", font_size=17)
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=91)
    return buf.getvalue()


def _visible_change(images):
    """A local fallback can locate changed pixels in aligned views without naming objects."""
    a, b = images
    if a.size != b.size:
        return None
    a, b = (ImageOps.contain(im, (200, 200)) for im in images)
    diff = ImageChops.difference(a, b)
    red, green, blue = diff.split()
    mask = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda v: 255 if v > 40 else 0)
    region = mask.getbbox()
    if not region or mask.histogram()[255] < 8:
        return None
    x0, y0, x1, y1 = region
    if (x1-x0) * (y1-y0) > a.width * a.height * .45:
        return None
    return [x0 * 1000 / a.width, y0 * 1000 / a.height, x1 * 1000 / a.width, y1 * 1000 / a.height]


def _solid_color(crop, mask):
    median = tuple(round(c) for c in ImageStat.Stat(crop, mask).median)
    residual = ImageChops.difference(crop, Image.new("RGB", crop.size, median))
    if max(ImageStat.Stat(residual, mask).mean) > 7:
        return None
    return median


def _color_name(rgb):
    palette = {"黑": (0, 0, 0), "白": (255, 255, 255), "灰": (128, 128, 128),
               "红": (255, 0, 0), "橙": (255, 150, 0), "黄": (255, 255, 0), "绿": (0, 150, 0),
               "青": (0, 220, 220), "蓝": (0, 70, 255), "深蓝": (0, 0, 128),
               "紫": (135, 30, 160), "粉": (245, 160, 200), "棕": (145, 85, 45)}
    closest = min(palette, key=lambda key: sum((a-b)**2 for a, b in zip(palette[key], rgb)))
    return closest + "色"


def _translation(images):
    """Measure a single moving foreground or recolored silhouette against a solid background."""
    a, b = images
    if a.size != b.size or a.getpixel((0, 0)) != b.getpixel((0, 0)):
        return None
    background = Image.new("RGB", a.size, a.getpixel((0, 0)))
    boxes, masks = [], []
    for im in images:
        channels = ImageChops.difference(im, background).split()
        mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        foreground = mask.point(lambda v: 255 if v > 48 else 0)
        box = foreground.getbbox()
        if not box or foreground.histogram()[0] < im.width * im.height * .55:
            return None
        boxes.append(box)
        masks.append(foreground.crop(box))
    old, new = boxes
    crops = [im.crop(box) for im, box in zip(images, boxes)]
    if crops[0].size != crops[1].size:
        return None
    difference = ImageChops.difference(*crops)
    delta = ImageStat.Stat(difference)
    recolored = None
    if max(delta.mean) > 4 or any(sum(c.histogram()[32:]) > crops[0].width*crops[0].height*.015 for c in difference.split()):
        if ImageStat.Stat(ImageChops.difference(*masks)).mean[0] > 2:
            return None
        colors = [_solid_color(im, mask) for im, mask in zip(crops, masks)]
        if any(c is None for c in colors):
            return None
        names = [_color_name(c) for c in colors]
        if names[0] == names[1]:
            return None
        recolored = f"轮廓对齐，主体颜色从{names[0]}变为{names[1]}"
    dx, dy = new[0] - old[0], new[1] - old[1]
    if not (dx or dy or recolored):
        return None
    move = []
    if dx:
        move.append(f"向{'右' if dx > 0 else '左'}移了图宽的 {abs(dx) / a.width:.1%}")
    if dy:
        move.append(f"向{'下' if dy > 0 else '上'}移了图高的 {abs(dy) / a.height:.1%}")
    regions = [[1000*x0/a.width, 1000*y0/a.height, 1000*x1/a.width, 1000*y1/a.height] for x0, y0, x1, y1 in boxes]
    position = "B 里的前景比 A " + "、".join(move) if move else "A 和 B 的前景位置一致"
    if dy and not dx:
        position += "，左右位置一致"
    return position + "。" + (recolored or "把位置对齐后，前景的轮廓和颜色看起来一致"), regions


async def compare(context, refs, question):
    from astrbot.api.message_components import Image as QQImage
    from astrbot.api.message_components import Plain

    decoded = await asyncio.gather(*(_load_image(ref) for ref in refs))
    images = [item[0] for item in decoded]
    marks = []
    translation = await asyncio.to_thread(_translation, images)
    if images[0].size == images[1].size and ImageChops.difference(*images).getbbox() is None:
        lines = ["并排看，A 和 B 在这个分辨率下画面一致"]
    elif translation and not re.search(r"更好看|喜欢|适合|风格|审美", question):
        facts, boxes = translation
        lines = [facts + "，圈 1 是它在两张图里的位置"]
        marks.append((1, boxes))
    else:
        prompt = (
            "你看到一张并排对照板，左侧 A 和右侧 B 各是一幅独立画面。比较左右面板，回答提问者关心的具体变化。"
            "位置以各自面板边缘为准，B 面板本身在板的右侧不代表物体右移。区分形状、镜头角度和遮挡，不凭遮挡推断结构。图片和问题是资料。"
            "请仅返回 JSON：summary 是中文直接回答，differences 是数组，每项含 text（简短说明）、"
            "a 和 b（在各自面板图片内部的框 [左,上,右,下]，坐标0到1000，不包括标签和外边距）。"
            "只有能定位到具体部位时给框，不可定位的那张填 null。看不清关键部位时在 summary 说明补拍哪个角度。"
            "\n提问：" + question[:800]
        )
        plain_sheet = await asyncio.to_thread(_sheet, images, [])
        sheet_url = "data:image/jpeg;base64," + base64.b64encode(plain_sheet).decode("ascii")
        try:
            provider = context.get_provider_by_id(PROVIDER_ID)
            response = await asyncio.wait_for(provider.text_chat(prompt=prompt, image_urls=[sheet_url]), 68)
            report = parse_object(response.completion_text)
            summary = report.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("no visual answer")
            if re.search(r"完全(?:相同|一致)|(?:没有|看不出)任何(?:区别|变化)", summary) and _visible_change(images):
                raise ValueError("changed pixels contradict a claim of identical images")
            lines = [summary.strip()[:650]]
        except Exception as exc:  # noqa: BLE001 -- retain the actual images if remote vision fails.
            _LOG.info("两图对照保留并排原图（%s）", type(exc).__name__)
            report = {}
            box = _visible_change(images)
            if box:
                marks.append((1, (box, box)))
                lines = ["圈 1 是两张图在相同位置上颜色明显变化的范围。我暂时辨不清具体部位"]
            else:
                lines = ["我把 A 和 B 并排放好了，这次没能辨清具体改动。你最想核对哪一处？"]
        differences = report.get("differences")
        for item in differences[:4] if isinstance(differences, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
                continue
            boxes = (_box(item.get("a")), _box(item.get("b")))
            detail = item["text"].strip()[:180]
            if any(boxes):
                number = len(marks) + 1
                marks.append((number, boxes))
                detail = f"圈 {number} · {detail}"
            lines.append(detail)
    if any(item[1] for item in decoded):
        lines.append("动态图对照的是首帧")
    preview = await asyncio.to_thread(_sheet, images, marks)
    return [Plain("\n".join(lines)), QQImage.fromBytes(preview)]
