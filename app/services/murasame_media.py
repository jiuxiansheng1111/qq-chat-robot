"""Local, user-provided Murasame image and emoji assets."""

import asyncio
import base64
import random
import time
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.config import Settings

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})
_PATH_CACHE: dict[tuple[str, str], tuple[float, tuple[Path, ...]]] = {}


def _asset_paths(settings: Settings, kind: str) -> list[Path]:
    subdir = (
        settings.murasame_emoji_subdir
        if kind == "emoji"
        else settings.murasame_image_subdir
    )
    root = Path(settings.murasame_asset_dir).expanduser() / subdir
    key = (str(root.resolve()), kind)
    now = time.monotonic()
    cached = _PATH_CACHE.get(key)
    if cached and now - cached[0] < 15:
        return list(cached[1])
    if not root.exists():
        paths: list[Path] = []
    else:
        paths = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]
    _PATH_CACHE[key] = (now, tuple(paths))
    return paths


def asset_help(settings: Settings) -> str:
    root = Path(settings.murasame_asset_dir).expanduser()
    return (
        "丛雨素材还没有放好。请把图片/GIF 放到\n"
        f"图片：{root / settings.murasame_image_subdir}\n"
        f"表情：{root / settings.murasame_emoji_subdir}\n"
        "然后发送“丛雨图片”或“丛雨表情”即可。"
    )


async def random_asset(settings: Settings, kind: str) -> str:
    paths = _asset_paths(settings, kind)
    if not paths:
        raise FileNotFoundError(asset_help(settings))
    path = random.SystemRandom().choice(paths)
    raw = await asyncio.to_thread(path.read_bytes)
    if len(raw) > settings.media_max_bytes:
        raise RuntimeError(f"素材超过 MEDIA_MAX_BYTES：{path.name}")
    try:
        with Image.open(BytesIO(raw)) as image:
            image.verify()
    except Exception as exc:
        raise RuntimeError(f"素材不是有效图片：{path.name}") from exc
    return "base64://" + base64.b64encode(raw).decode("ascii")


def asset_count(settings: Settings) -> tuple[int, int]:
    return len(_asset_paths(settings, "image")), len(_asset_paths(settings, "emoji"))
