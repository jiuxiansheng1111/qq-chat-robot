"""有界的音乐候选卡片与歌词图片渲染。"""

from __future__ import annotations

import asyncio
import base64
import html
import io
import re
import struct
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from app.services.music import NeteaseTrack


_CARD_WIDTH = 220
_CARD_HEIGHT = 300
_THUMB_HEIGHT = 220
_CARD_MARGIN = 16
_CARD_CORNER_RADIUS = 10
_CARD_FONT_SIZE = 16
_MAX_CANDIDATES = 5
_MAX_COVER_BYTES = 4 * 1024 * 1024
_MAX_COVER_PIXELS = 4_000_000
_MAX_IMAGE_BYTES = 2 * 1024 * 1024
_LYRICS_WIDTH = 1000
_LYRICS_MARGIN_X = 80
_LYRICS_MAX_HEIGHT = 1600
_LYRICS_MAX_LINES_PER_PAGE = 28
_LYRICS_MAX_PAGES = 8
_LYRICS_MAX_CHARS = 12_000
_LYRICS_MAX_INPUT_LINES = 256
_LYRICS_TIME_TAG = re.compile(r"\[\d{1,3}:\d{2}(?:\.\d{1,3})?\]")
_LYRICS_META_TAG = re.compile(r"\[[A-Za-z][A-Za-z0-9_-]*\s*:[^\]]*\]")
_LYRICS_META_LINE = re.compile(r"^\s*\[[A-Za-z][A-Za-z0-9_-]*\s*:[^\]]*\]\s*$")
_HTML_TAG = re.compile(r"<[^>]{0,256}>")
_BREAK_CHARS = frozenset(" ，、。！？；：,.!?;:")
_RENDER_LOCK = asyncio.Lock()
_RenderResult = TypeVar("_RenderResult")


async def render_music_candidates(
    tracks: list[NeteaseTrack],
    covers: dict[str, bytes] | None = None,
) -> str:
    """用最多五首候选绘制静态卡片，封面由调用方提供。"""
    selected = list(tracks[:_MAX_CANDIDATES])
    if not selected:
        raise ValueError("没有可绘制的歌曲候选。")

    selected_covers = {
        track.cover_url: covers[track.cover_url]
        for track in selected
        if covers is not None and track.cover_url in covers
    }
    image_bytes = await _run_serialized(_render_music_candidates_sync, selected, selected_covers)
    return _as_base64_image(image_bytes)


async def render_music_lyrics(track: NeteaseTrack, lrc: str) -> list[str]:
    """把完整 LRC 分页绘制；输入或分页超限时明确报错。"""
    if not isinstance(lrc, str):
        raise TypeError("歌词必须是文本。")
    if len(lrc) > _LYRICS_MAX_CHARS:
        raise ValueError("歌词超过 12000 字符限制，未进行截断。")
    source_lines = lrc.splitlines()
    if len(source_lines) > _LYRICS_MAX_INPUT_LINES:
        raise ValueError("歌词超过 256 行限制，未进行截断。")

    cleaned_lines = _clean_lrc_lines(source_lines)
    if not cleaned_lines:
        raise ValueError("歌词中没有可显示的正文。")

    images = await _run_serialized(_render_music_lyrics_sync, track, cleaned_lines)
    return [_as_base64_image(image_bytes) for image_bytes in images]


async def _run_serialized(function: Callable[..., _RenderResult], *args: object) -> _RenderResult:
    """取消调用时等后台绘制结束，再释放全局渲染闸门。"""
    async with _RENDER_LOCK:
        worker = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.wait({worker}, return_when=asyncio.ALL_COMPLETED)
                except asyncio.CancelledError:
                    continue
            if worker.done() and not worker.cancelled():
                worker.exception()
            raise


def _render_music_candidates_sync(tracks: list[NeteaseTrack], covers: dict[str, bytes]) -> bytes:
    from app.services.help_menu import _find_font_path

    theme = {
        "card_bg": "#ffffff",
        "canvas_bg": "#f5f5f5",
        "title_color": "#000000",
        "sub_text_color": "#666666",
        "overlay_text_color": "#ffffff",
        "gradient_height": 40,
        "gradient_max_alpha": 180,
    }
    font_path = str(_find_font_path())
    font = ImageFont.truetype(font_path, _CARD_FONT_SIZE)
    badge_font = ImageFont.truetype(font_path, 18)
    cards = [
        _draw_candidate_card(track, index, covers, font, badge_font, theme)
        for index, track in enumerate(tracks, start=1)
    ]

    rows: list[Image.Image] = []
    cards_per_row = 3
    for offset in range(0, len(cards), cards_per_row):
        row_cards = cards[offset : offset + cards_per_row]
        row_width = cards_per_row * _CARD_WIDTH + (cards_per_row + 1) * _CARD_MARGIN
        row = Image.new(
            "RGBA",
            (row_width, _CARD_HEIGHT + 2 * _CARD_MARGIN),
            theme["canvas_bg"],
        )
        for column, card in enumerate(row_cards):
            x = _CARD_MARGIN + column * (_CARD_WIDTH + _CARD_MARGIN)
            row.paste(card, (x, _CARD_MARGIN), card)
        rows.append(row)

    canvas_height = sum(row.height for row in rows)
    canvas = Image.new("RGBA", (rows[0].width, canvas_height), theme["canvas_bg"])
    y_offset = 0
    for row in rows:
        canvas.paste(row, (0, y_offset), row)
        y_offset += row.height

    final_image = Image.new("RGB", canvas.size, theme["canvas_bg"])
    final_image.paste(canvas, mask=canvas.getchannel("A"))
    return _encode_jpeg(final_image)


def _draw_candidate_card(
    track: NeteaseTrack,
    index: int,
    covers: dict[str, bytes],
    font: ImageFont.FreeTypeFont,
    badge_font: ImageFont.FreeTypeFont,
    theme: dict[str, str | int],
) -> Image.Image:
    card = Image.new("RGBA", (_CARD_WIDTH, _CARD_HEIGHT), theme["card_bg"])
    draw = ImageDraw.Draw(card)
    cover = _decode_static_cover(covers.get(track.cover_url, b""))
    if cover is None:
        thumb = Image.new("RGB", (_CARD_WIDTH, _THUMB_HEIGHT), "#e5e5e5")
    else:
        thumb = cover.resize((_CARD_WIDTH, _THUMB_HEIGHT), Image.Resampling.LANCZOS)
    card.paste(thumb, (0, 0))

    gradient_height = int(theme["gradient_height"])
    gradient = Image.new("L", (1, gradient_height))
    max_alpha = int(theme["gradient_max_alpha"])
    gradient.putdata(
        [int(max_alpha * (y / max(1, gradient_height))) for y in range(gradient_height)]
    )
    gradient = gradient.resize((_CARD_WIDTH, gradient_height))
    overlay = Image.new("RGBA", (_CARD_WIDTH, gradient_height), (0, 0, 0, 255))
    overlay.putalpha(gradient)
    card.alpha_composite(overlay, (0, _THUMB_HEIGHT - gradient_height))

    draw.text(
        (_CARD_WIDTH - 40, _THUMB_HEIGHT - 20),
        _format_duration(track.duration_seconds),
        font=font,
        fill=theme["overlay_text_color"],
    )

    title = _wrap_candidate_title(draw, _plain_text(track.title), font, _CARD_WIDTH - 16)
    draw.text(
        (8, _THUMB_HEIGHT + 8),
        title,
        font=font,
        fill=theme["title_color"],
    )
    artist = _plain_text(track.artist)[:240] or "-"
    artist = _fit_text(draw, artist, font, _CARD_WIDTH - 48)
    draw.text(
        (8, _CARD_HEIGHT - 30),
        artist,
        font=font,
        fill=theme["sub_text_color"],
    )
    _draw_index_badge(draw, index, badge_font)

    mask = Image.new("L", (_CARD_WIDTH, _CARD_HEIGHT), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, _CARD_WIDTH, _CARD_HEIGHT),
        radius=_CARD_CORNER_RADIUS,
        fill=255,
    )
    card.putalpha(mask)
    return card


def _wrap_candidate_title(
    draw: ImageDraw.ImageDraw,
    title: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    remaining = title or "未知歌曲"
    lines: list[str] = []
    for _ in range(2):
        if _text_width(draw, remaining, font) <= max_width:
            lines.append(remaining)
            remaining = ""
            break
        low, high = 1, len(remaining)
        while low < high:
            middle = (low + high + 1) // 2
            if _text_width(draw, remaining[:middle], font) <= max_width:
                low = middle
            else:
                high = middle - 1
        cut = max(1, low)
        natural_cut = max(
            (
                index + 1
                for index, character in enumerate(remaining[:cut])
                if character.isspace() or character in _BREAK_CHARS
            ),
            default=0,
        )
        if natural_cut:
            cut = natural_cut
        lines.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
        if not remaining:
            break
    if remaining:
        last = lines[-1]
        while last and _text_width(draw, last + "…", font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return "\n".join(lines)


def _draw_index_badge(draw: ImageDraw.ImageDraw, index: int, font: ImageFont.FreeTypeFont) -> None:
    badge_width = 34
    badge_height = 30
    left = _CARD_WIDTH - badge_width - 8
    top = _CARD_HEIGHT - badge_height - 8
    draw.rounded_rectangle(
        (left, top, left + badge_width, top + badge_height),
        radius=10,
        fill="#317166",
    )
    label = str(index)
    bbox = draw.textbbox((0, 0), label, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text(
        (
            left + (badge_width - width) / 2 - bbox[0],
            top + (badge_height - height) / 2 - bbox[1],
        ),
        label,
        font=font,
        fill="white",
    )


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _decode_static_cover(data: bytes) -> Image.Image | None:
    if not isinstance(data, bytes) or not data or len(data) > _MAX_COVER_BYTES:
        return None
    try:
        with Image.open(io.BytesIO(data)) as source:
            width, height = source.size
            if width < 1 or height < 1 or width * height > _MAX_COVER_PIXELS:
                return None
            # 只读取首帧，避免展开动画帧。
            source.seek(0)
            source.load()
            return source.convert("RGB")
    except (
        Image.DecompressionBombError,
        OSError,
        ValueError,
        EOFError,
        SyntaxError,
        struct.error,
    ):
        return None


def _render_music_lyrics_sync(track: NeteaseTrack, lines: list[str]) -> list[bytes]:
    from app.services.help_menu import _find_font_path

    font_path = str(_find_font_path())
    font = ImageFont.truetype(font_path, 28)
    title_font = ImageFont.truetype(font_path, 24)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lyric_top = 100
    bottom_padding = 52
    visual_lines: list[tuple[str, int]] = []
    for line in lines:
        wrapped_lines = _wrap_line_by_width(
            measure, line, font, _LYRICS_WIDTH - 2 * _LYRICS_MARGIN_X
        )
        for visual_line in wrapped_lines:
            text = visual_line if visual_line else "　"
            bbox = measure.textbbox((0, 0), text, font=font)
            visual_lines.append((visual_line, max(32, bbox[3] - bbox[1])))
        if len(visual_lines) > _LYRICS_MAX_PAGES * _LYRICS_MAX_LINES_PER_PAGE:
            raise ValueError("歌词排版超过 8 页限制，未截断正文。")

    pages: list[list[tuple[str, int]]] = []
    current_page: list[tuple[str, int]] = []
    current_height = lyric_top + bottom_padding
    for line in visual_lines:
        next_height = current_height + line[1] + 8
        if current_page and (
            len(current_page) >= _LYRICS_MAX_LINES_PER_PAGE or next_height > _LYRICS_MAX_HEIGHT
        ):
            pages.append(current_page)
            current_page = []
            current_height = lyric_top + bottom_padding
            next_height = current_height + line[1] + 8
        if next_height > _LYRICS_MAX_HEIGHT:
            raise ValueError("单行歌词无法放入页面，未截断正文。")
        current_page.append(line)
        current_height = next_height
    if current_page:
        pages.append(current_page)
    if not pages or len(pages) > _LYRICS_MAX_PAGES:
        raise ValueError("歌词排版超过 8 页限制，未截断正文。")

    title = _plain_text(track.title) or "歌词"
    artist = _plain_text(track.artist)[:240]
    header = f"{title} · {artist}" if artist else title
    header = _fit_text(measure, header, title_font, _LYRICS_WIDTH - 2 * _LYRICS_MARGIN_X)
    return [
        _draw_lyrics_page(
            page,
            page_number,
            len(pages),
            header,
            font,
            title_font,
            lyric_top,
            bottom_padding,
        )
        for page_number, page in enumerate(pages, start=1)
    ]


def _draw_lyrics_page(
    lines: list[tuple[str, int]],
    page_number: int,
    page_count: int,
    header: str,
    font: ImageFont.FreeTypeFont,
    title_font: ImageFont.FreeTypeFont,
    lyric_top: int,
    bottom_padding: int,
) -> bytes:
    page_height = lyric_top + sum(height + 8 for _, height in lines) + bottom_padding
    if len(lines) > _LYRICS_MAX_LINES_PER_PAGE or page_height > _LYRICS_MAX_HEIGHT:
        raise ValueError("歌词页面超过行数或高度限制，未截断正文。")

    top_color = (255, 250, 240)
    bottom_color = (235, 255, 247)
    gradient = Image.new("RGB", (1, page_height))
    gradient.putdata(
        [
            (
                int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio),
                int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio),
                int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio),
            )
            for ratio in (y / max(1, page_height - 1) for y in range(page_height))
        ]
    )
    image = gradient.resize((_LYRICS_WIDTH, page_height))
    draw = ImageDraw.Draw(image)
    draw.text(
        (_LYRICS_MARGIN_X, 28),
        header,
        font=title_font,
        fill=(70, 70, 70),
    )
    y = lyric_top
    for line, line_height in lines:
        text = line if line else "　"
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(
            (_LYRICS_MARGIN_X, y - bbox[1]),
            text,
            font=font,
            fill=(70, 70, 70),
        )
        y += line_height + 8
    footer = f"{page_number}/{page_count}"
    footer_bbox = draw.textbbox((0, 0), footer, font=title_font)
    draw.text(
        (_LYRICS_WIDTH - _LYRICS_MARGIN_X - (footer_bbox[2] - footer_bbox[0]), page_height - 40),
        footer,
        font=title_font,
        fill=(90, 90, 90),
    )
    return _encode_jpeg(image)


def _clean_lrc_lines(source_lines: list[str]) -> list[str]:
    result: list[str] = []
    for source_line in source_lines:
        if _LYRICS_META_LINE.fullmatch(source_line):
            continue
        line = _LYRICS_TIME_TAG.sub("", source_line)
        line = _LYRICS_META_TAG.sub("", line).strip()
        if line:
            result.append(line)
        elif result and result[-1]:
            result.append("")
    while result and not result[-1]:
        result.pop()
    return result


def _wrap_line_by_width(
    draw: ImageDraw.ImageDraw,
    line: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    if not line:
        return [""]
    result: list[str] = []
    remaining = line
    while remaining:
        if draw.textlength(remaining, font=font) <= max_width:
            result.append(remaining)
            break
        low, high = 1, len(remaining)
        while low < high:
            middle = (low + high + 1) // 2
            if draw.textlength(remaining[:middle], font=font) <= max_width:
                low = middle
            else:
                high = middle - 1
        cut = max(1, low)
        natural_cut = max(
            (
                index + 1
                for index, character in enumerate(remaining[:cut])
                if character in _BREAK_CHARS
            ),
            default=0,
        )
        if natural_cut:
            cut = natural_cut
        result.append(remaining[:cut])
        remaining = remaining[cut:]
    return result


def _plain_text(value: str) -> str:
    untagged = _HTML_TAG.sub("", html.unescape(str(value or "")))
    return " ".join(untagged.split())


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "…"
    while text and draw.textlength(text + suffix, font=font) > max_width:
        text = text[:-1]
    return text + suffix


def _format_duration(duration_seconds: int) -> str:
    seconds = max(0, int(duration_seconds or 0))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


def _encode_jpeg(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=82, optimize=True)
    encoded = output.getvalue()
    if len(encoded) > _MAX_IMAGE_BYTES:
        raise ValueError("图片超过 2 MiB 编码上限。")
    return encoded


def _as_base64_image(image_bytes: bytes) -> str:
    return "base64://" + base64.b64encode(image_bytes).decode("ascii")
