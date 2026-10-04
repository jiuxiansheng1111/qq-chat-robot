"""绘制简洁的中文帮助菜单图片。"""

import asyncio
import base64
import hashlib
import io
import os
import re
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BACKGROUND_DEFAULT = "assets/help-menu-background.png"
_CANVAS_WIDTH = 1080
_MIN_CANVAS_HEIGHT = 1480
_MAX_ENCODED_BYTES = 4 * 1024 * 1024
_CACHE_VERSION = "help-menu-v2"
_MENU_TITLE = "聊天机器人 · 功能菜单"
_IMAGE_MENU_SUBTITLE = "先 @我，再说想做什么；如“给我看看菜单”"
_TEXT_MENU_SUBTITLE = "先 @我，再说想做什么。"
_MENU_REQUEST_WORDS = (
    "给我看看", "让我看看", "帮我看看", "帮我看", "让我看", "给我看", "看看", "看一下", "查看", "想看", "要看",
    "想要", "我要", "给我", "发给我", "发我", "发一份", "发一张", "发张", "发一下",
    "发下", "发个", "帮我发", "给我发", "来一份", "来份", "来个", "来张", "给个",
    "显示", "列一下", "列出", "查一下", "告诉我", "怎么用", "是什么", "是啥",
)
_MENU_NEGATION_RE = re.compile(
    r"(?:不要|别再|别|不想|不需要|不必|不用|无需|不发|不看|不关|不开|不打开|不关闭|"
    r"不能|不会|先别|先不)"
)

_IMAGE_GROUPS = (
    (
        "聊天陪伴",
        ("聊天：@我 + 想说的话", "角色互动：芳乃出来", "语音：启动语音 / 关闭语音"),
        (92, 117, 151),
    ),
    (
        "语音翻唱",
        ("选角色：可用角色 / 选择角色 芳乃", "短版：翻唱片段 芳乃 歌名（约20秒）", "整首：翻唱完整 芳乃 歌名（每段<2分钟）"),
        (177, 108, 145),
    ),
    (
        "点歌视频",
        ("点歌：@我 点歌 歌手 歌名", "B站：@我 播放视频 关键词"),
        (94, 147, 147),
    ),
    (
        "实用工具",
        ("天气：@我 天气 上海", "翻译 / 搜索：@我 翻译… / 搜索…", "发图：随机猫咪 / 随机二次元图"),
        (129, 133, 188),
    ),
    (
        "互动小游戏",
        ("抽角色：今日奥特曼 / 随机二次元角色", "图鉴：@我 图鉴 / 角色图鉴", "互动：石头 vs 剪刀谁赢？ / @我 随机夺舍"),
        (199, 151, 94),
    ),
    (
        "管理",
        ("开机器人：/bot on", "关机器人：/bot off", "黑名单：/blacklist add|remove QQ号"),
        (122, 137, 158),
    ),
)

_TEXT_GROUPS = (
    (
        "聊天陪伴",
        (
            "@我 + 想聊的话；也可直接叫角色名，如“芳乃出来”“芦花姐陪我聊聊”。",
            "开启恋爱模式 / 关闭恋爱模式；发送“好感度”查看好感。",
            "“记住：内容”保存信息；“删除记忆 关键词”删除记忆。",
        ),
    ),
    (
        "语音翻唱",
        (
            "“启动语音”“关闭语音”“语音状态”控制角色语音。",
            "“可用角色”查看角色；“选择角色 芳乃”切换角色。",
            "@我 翻唱片段 芳乃 歌名：约20秒。@我 翻唱完整 芳乃 歌名：整首分段发送，每段少于2分钟（上限115秒）。",
            "“唱歌状态”查看进度，“取消唱歌”停止，“唱歌音色”查看可用音色。",
        ),
    ),
    (
        "点歌视频",
        (
            "@我 点歌 歌手 歌名，或 @我 来首 歌名。",
            "@我 播放视频 关键词：搜索并发送 B 站视频。",
        ),
    ),
    (
        "实用工具",
        (
            "@我 天气 上海；@我 翻译 外语内容；@我 搜索 关键词。",
            "@我 今日热点 查看新闻；@我 随机猫咪 / 随机猪猪 / 随机奶龙 获取图片。",
            "@我 生成图片 一只在月光下撑伞的猫娘。",
            "AstrBot 图片插件：来张随机二次元图片；角色图鉴和收藏照常使用。",
        ),
    ),
    (
        "互动小游戏",
        (
            "@我 今日奥特曼 / 本命奥特曼；@我 随机二次元角色 / 本命二次元角色。",
            "@我 图鉴 / 角色图鉴 查看日漫与特摄图鉴；@我 角色图鉴 捷德（也可换成“雷姆”）；也可问“石头 vs 剪刀谁赢？”。",
            "@我 随机夺舍，或 @我 夺舍 @群成员；本人或管理员可 @我 退出。",
        ),
    ),
    (
        "管理",
        (
            "群管理仅限管理员：/bot on|off。",
            "黑名单仅限管理员：/blacklist add|remove QQ号。",
        ),
    ),
)

_TEXT_MENU_ALIASES = frozenset({"文字版菜单", "文字菜单", "文字版帮助", "菜单文字版"})


def _normalize_image_groups(
    groups: Sequence[tuple[str, Sequence[str], Sequence[int]]] | None,
) -> tuple[tuple[str, tuple[str, ...], tuple[int, int, int]], ...]:
    selected = _IMAGE_GROUPS if groups is None else groups
    normalized = []
    for heading, lines, accent in selected:
        color = tuple(int(value) for value in accent)
        if len(color) != 3:
            raise ValueError("菜单卡片颜色应包含三个 RGB 分量")
        normalized.append((str(heading), tuple(str(line) for line in lines), color))
    return tuple(normalized)


def _normalize_text_groups(
    groups: Sequence[tuple[str, Sequence[str]]] | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    selected = _TEXT_GROUPS if groups is None else groups
    return tuple(
        (str(heading), tuple(str(line) for line in lines))
        for heading, lines in selected
    )


def _menu_request_text(text: str) -> str:
    value = " ".join(text.strip().split())
    if value.startswith("/"):
        value = value[1:].strip()
        for command in ("help", "帮助", "菜单", "功能菜单"):
            if value.casefold().startswith(command.casefold() + " "):
                value = value[len(command):].strip()
                break
    return value


def _has_menu_request(value: str) -> bool:
    compact = re.sub(r"\s+", "", value).casefold()
    return not _MENU_NEGATION_RE.search(compact) and any(
        word in compact for word in _MENU_REQUEST_WORDS
    )


def concise_text_menu(
    text_groups: Sequence[tuple[str, Sequence[str]]] | None = None,
    *,
    title: str | None = None,
    subtitle: str | None = None,
) -> str:
    """返回分组清楚的完整文字菜单。"""
    sections = [title or _MENU_TITLE, subtitle or _TEXT_MENU_SUBTITLE]
    for heading, lines in _normalize_text_groups(text_groups):
        sections.append(f"\n【{heading}】")
        sections.extend(f"- {line}" for line in lines)
    return "\n".join(sections)


def is_text_menu_request(text: str) -> bool:
    """识别文字菜单的命令和简短自然请求。"""
    if not isinstance(text, str):
        return False
    value = _menu_request_text(text)
    if value in _TEXT_MENU_ALIASES:
        return True
    if len(value) > 40:
        return False
    return _has_menu_request(value) and any(alias in value for alias in _TEXT_MENU_ALIASES)


def is_help_menu_request(text: str) -> bool:
    """识别图片菜单命令和简短自然请求。"""
    if not isinstance(text, str) or is_text_menu_request(text):
        return False
    value = _menu_request_text(text)
    if value in {"help", "帮助", "帮忙", "功能", "菜单", "功能菜单", "/help", "/帮助", "/菜单", "/功能菜单"}:
        return True
    if len(value) > 40:
        return False
    compact = re.sub(r"\s+", "", value).casefold()
    help_words = ("帮助", "菜单", "功能菜单", "功能", "你会什么", "你能干嘛", "能干嘛", "指令")
    if compact in {"帮忙", "功能", "你会什么", "你能干嘛", "能干嘛", "有什么功能", "有哪些功能", "指令", "命令"}:
        return True
    return (
        not _MENU_NEGATION_RE.search(compact)
        and any(word in compact for word in help_words)
        and any(word in compact for word in _MENU_REQUEST_WORDS)
    )


def _font_candidates() -> tuple[Path, ...]:
    """按平台返回支持中文的字体候选。"""
    windows = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    )
    other = (
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
    )
    return windows + other


def _find_font_path() -> Path:
    for path in _font_candidates():
        if path.is_file():
            try:
                ImageFont.truetype(str(path), 24)
            except OSError:
                continue
            return path
    raise RuntimeError(
        "无法生成帮助菜单图片：没有找到支持中文的字体。"
        "请安装微软雅黑、思源黑体、苹方或文泉驿字体后重试。"
    )


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _background_path(settings: Any) -> Path:
    configured = getattr(settings, "help_menu_background_path", _BACKGROUND_DEFAULT)
    raw = str(configured or _BACKGROUND_DEFAULT).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


def _gradient_background(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    pixels = image.load()
    top = (246, 249, 247)
    bottom = (229, 241, 234)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(round(a * (1 - ratio) + b * ratio) for a, b in zip(top, bottom))
        for x in range(width):
            pixels[x, y] = color
    draw = ImageDraw.Draw(image, "RGBA")
    draw.ellipse((-220, height - 440, 450, height + 230), fill=(208, 232, 221, 70))
    draw.ellipse((700, -260, 1340, 380), fill=(224, 237, 235, 90))
    return image


def _load_background(path: Path, size: tuple[int, int]) -> Image.Image:
    try:
        with Image.open(path) as source:
            return ImageOps.fit(
                source.convert("RGB"),
                size,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except (OSError, ValueError):
        return _gradient_background(size)


def _draw_centered(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
) -> None:
    draw.text(xy, text, font=font, fill=fill, anchor="mm")


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    max_width: int,
    size: int = 25,
    min_size: int = 19,
) -> ImageFont.FreeTypeFont:
    for current_size in range(size, min_size - 1, -1):
        font = _load_font(font_path, current_size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return _load_font(font_path, min_size)


def _fit_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    max_width: int,
    size: int = 25,
    min_size: int = 19,
) -> tuple[ImageFont.FreeTypeFont, tuple[str, ...]]:
    for current_size in range(size, min_size - 1, -1):
        font = _load_font(font_path, current_size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font, (text,)

    font = _load_font(font_path, min_size)
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current or not lines:
        lines.append(current)
    return font, tuple(lines)


def _draw_menu(
    background_path: Path,
    image_groups: tuple[tuple[str, tuple[str, ...], tuple[int, int, int]], ...],
    title: str,
    subtitle: str,
) -> Image.Image:
    font_path = _find_font_path()
    width = _CANVAS_WIDTH

    left = 56
    top = 215
    gap_x = 24
    gap_y = 22
    card_width = (width - 2 * left - gap_x) // 2
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    prepared_groups = []
    card_height = 332
    for heading, lines, accent in image_groups:
        prepared_lines = []
        content_height = 0
        for line in lines:
            font, wrapped = _fit_wrapped_text(
                measure, line, font_path, card_width - 87,
            )
            prepared_lines.append((font, wrapped))
            content_height += max(61, 30 * len(wrapped))
        card_height = max(card_height, 105 + content_height + 25)
        prepared_groups.append((heading, prepared_lines, accent))

    rows = (len(prepared_groups) + 1) // 2
    content_bottom = top + rows * (card_height + gap_y) - gap_y if rows else top
    note_y = content_bottom + 70
    footer_y = note_y + 47
    canvas_height = max(_MIN_CANVAS_HEIGHT, footer_y + 79)
    canvas = _load_background(background_path, (width, canvas_height)).convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")
    title_font = _fit_font(draw, title, font_path, width - 116, size=49, min_size=32)
    subtitle_font = _fit_font(draw, subtitle, font_path, width - 122, size=29, min_size=19)
    draw.text((58, 54), title, font=title_font, fill=(37, 54, 68, 255))
    draw.text((61, 127), subtitle, font=subtitle_font, fill=(80, 103, 108, 245))
    draw.rounded_rectangle((59, 184, width - 59, 188), radius=2, fill=(91, 151, 132, 150))

    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    cards = []
    for index, (heading, lines, accent) in enumerate(prepared_groups):
        row, column = divmod(index, 2)
        x = left + column * (card_width + gap_x)
        y = top + row * (card_height + gap_y)
        cards.append((x, y, heading, lines, accent))
        shadow_draw.rounded_rectangle(
            (x + 2, y + 8, x + card_width + 2, y + card_height + 8),
            radius=28,
            fill=(45, 63, 60, 30),
        )
    canvas = Image.alpha_composite(canvas, shadow)
    card_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(card_layer, "RGBA")

    for index, (x, y, heading, lines, accent) in enumerate(cards):
        draw.rounded_rectangle(
            (x, y, x + card_width, y + card_height),
            radius=28,
            fill=(255, 255, 255, 229),
            outline=(255, 255, 255, 242),
            width=2,
        )
        draw.ellipse((x + 27, y + 24, x + 68, y + 65), fill=(*accent, 255))
        number_font = _load_font(font_path, 18)
        _draw_centered(draw, (x + 47, y + 45), f"{index + 1:02d}", number_font, (255, 255, 255))
        heading_font = _fit_font(
            draw, heading, font_path, card_width - 120, size=32, min_size=21,
        )
        draw.text((x + 82, y + 27), heading, font=heading_font, fill=(41, 55, 68, 255))

        line_offset = 0
        for font, wrapped in lines:
            line_y = y + 105 + line_offset
            draw.ellipse((x + 32, line_y + 10, x + 43, line_y + 21), fill=(*accent, 230))
            for wrapped_index, part in enumerate(wrapped):
                draw.text(
                    (x + 57, line_y + wrapped_index * 30), part,
                    font=font, fill=(53, 67, 78, 255),
                )
            line_offset += max(61, 30 * len(wrapped))

    canvas = Image.alpha_composite(canvas, card_layer)
    draw = ImageDraw.Draw(canvas, "RGBA")

    footer_font = _load_font(font_path, 29)
    note_font = _load_font(font_path, 25)
    note = "短版约 20 秒 · 整首逐段发送，每段不到 2 分钟"
    note_bbox = draw.textbbox((0, 0), note, font=note_font)
    draw.text(
        ((width - (note_bbox[2] - note_bbox[0])) // 2, note_y), note,
        font=note_font, fill=(64, 83, 92, 255),
    )
    footer_text = "想看全部用法：@我 文字版菜单"
    footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
    draw.text(
        ((width - (footer_bbox[2] - footer_bbox[0])) // 2, footer_y),
        footer_text,
        font=footer_font,
        fill=(50, 83, 76, 255),
    )
    return canvas.convert("RGB")


def _cache_key(
    background: Path,
    image_groups: tuple[tuple[str, tuple[str, ...], tuple[int, int, int]], ...],
    text_groups: tuple[tuple[str, tuple[str, ...]], ...],
    title: str,
    subtitle: str,
) -> str:
    info = "missing"
    try:
        stat = background.stat()
        info = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        pass
    source_mtime = Path(__file__).stat().st_mtime_ns
    raw = "\n".join((
        _CACHE_VERSION,
        str(background),
        info,
        str(source_mtime),
        concise_text_menu(text_groups, title=title, subtitle=subtitle),
        repr(image_groups),
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _render_help_menu_sync(
    settings: Any,
    image_groups: Sequence[tuple[str, Sequence[str], Sequence[int]]] | None = None,
    text_groups: Sequence[tuple[str, Sequence[str]]] | None = None,
    title: str | None = None,
    subtitle: str | None = None,
) -> str:
    normalized_image_groups = _normalize_image_groups(image_groups)
    normalized_text_groups = _normalize_text_groups(text_groups)
    resolved_title = title or _MENU_TITLE
    resolved_subtitle = subtitle or _IMAGE_MENU_SUBTITLE
    background_path = _background_path(settings)
    cache_dir = _PROJECT_ROOT / "data" / "help_menu"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = _cache_key(
        background_path,
        normalized_image_groups,
        normalized_text_groups,
        resolved_title,
        resolved_subtitle,
    )
    cache_path = cache_dir / f"menu_{cache_key}.png"
    try:
        if cache_path.is_file():
            cached = cache_path.read_bytes()
            encoded = base64.b64encode(cached)
            if (
                encoded.startswith(b"iVBOR")
                and len(encoded) + len(b"base64://") <= _MAX_ENCODED_BYTES
            ):
                return "base64://" + encoded.decode("ascii")
    except OSError:
        pass

    rendered = _draw_menu(
        background_path, normalized_image_groups, resolved_title, resolved_subtitle,
    )
    png_bytes = b""
    for color_count in (256, 192, 128, 96, 64, 32):
        output = io.BytesIO()
        rendered.quantize(colors=color_count).save(output, format="PNG", optimize=True)
        png_bytes = output.getvalue()
        encoded = base64.b64encode(png_bytes)
        if len(encoded) + len(b"base64://") <= _MAX_ENCODED_BYTES:
            break
    else:
        raise RuntimeError("帮助菜单图片压缩后仍超过 4 MiB。")

    temporary = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(png_bytes)
        os.replace(temporary, cache_path)
    finally:
        temporary.unlink(missing_ok=True)
    return "base64://" + encoded.decode("ascii")


async def render_help_menu(
    settings: Any,
    *,
    image_groups: Sequence[tuple[str, Sequence[str], Sequence[int]]] | None = None,
    text_groups: Sequence[tuple[str, Sequence[str]]] | None = None,
    title: str | None = None,
    subtitle: str | None = None,
) -> str:
    """在线程中绘制或读取缓存，返回 OneBot base64 图片地址。"""
    return await asyncio.to_thread(
        _render_help_menu_sync, settings, image_groups, text_groups, title, subtitle,
    )
