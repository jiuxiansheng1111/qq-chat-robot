"""用当前免费绘图后端逐格绘制，再排版为单话题连环漫画。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.astrbot_comic import normalize_comic_topics


class ComicGenerationError(RuntimeError):
    """上层只向用户说明失败阶段，不泄露接口错误或凭据。"""


def free_comic_provider(config: Any) -> dict:
    """只使用已配置的官方免费 CogView，不遍历可能收费的后备供应商。"""
    values = config.get_drawing_provider_configs()
    if not isinstance(values, list) or not values or not isinstance(values[0], Mapping):
        raise ComicGenerationError("未配置免费漫画绘图接口")
    provider = dict(values[0])
    url = urlsplit(str(provider.get("api_url", "")))
    if (
        str(provider.get("model", "")).casefold() != "cogview-3-flash"
        or url.scheme != "https" or url.hostname != "open.bigmodel.cn"
        or url.username or url.password or url.query or url.fragment or url.port not in (None, 443)
        or url.path.rstrip("/") != "/api/paas/v4/images/generations"
        or str(provider.get("api_protocol", "images")) != "images"
        or not str(provider.get("api_key", "")).strip()
    ):
        raise ComicGenerationError("当前漫画接口不符合免费官方绘图配置")
    provider.update(timeout=35, image_size="1024x1024", image_quality="standard")
    return provider


class _SingleFreeProvider:
    def __init__(self, config: Any, provider: dict) -> None:
        self._config, self._provider = config, provider

    def get_drawing_provider_configs(self) -> list[dict]:
        return [dict(self._provider)]

    def get_drawing_network_retries(self) -> int:
        return 0

    def get_drawing_output_exception_retries(self) -> int:
        return 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._config, name)


async def generate_illustrated_comic(
    topics: Any, config: Any, drawing_client: Any, output_dir: str | Path,
    *, character_name: str = "丛雨", deadline_seconds: float = 150,
    source_window: Mapping | None = None,
) -> tuple[bytes, dict]:
    """三次以内顺序出图；任一格失败就终止，不用说明卡替代漫画。"""
    from app.services.group_comic_check import verify_group_comic_panels
    from app.services.group_comic_layout import render_group_comic
    from app.services.group_comic_story import build_group_comic_story
    from app.services.latest_comic_source import comic_source_time_note

    story = build_group_comic_story(topics, character_name=character_name)
    if story.get("status") != "ready" or len(story.get("panels", [])) != 3:
        raise ComicGenerationError("没有足够具体、适合改编成连贯漫画的群聊素材")
    provider = free_comic_provider(config)
    client = type(drawing_client)(_SingleFreeProvider(config, provider))
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    images: list[bytes] = []
    digests: set[str] = set()
    try:
        async with asyncio.timeout(min(150.0, max(1.0, float(deadline_seconds)))):
            for index, panel in enumerate(story["panels"], 1):
                async with asyncio.timeout(42):
                    image, _error = await client.generate_image(
                        panel["drawing_prompt"], images_data=None, disable_retry=True,
                    )
                if not isinstance(image, bytes) or not image:
                    raise ComicGenerationError(f"第{index}格绘图未完成")
                digest = hashlib.sha256(image).hexdigest()
                if digest in digests:
                    raise ComicGenerationError("绘图接口返回了重复画面")
                digests.add(digest)
                images.append(image)
                (directory / f"panel-{index}-{digest[:16]}.img").write_bytes(image)
            get_proxy = getattr(client, "_get_request_proxy", None)
            proxy = get_proxy(provider) if callable(get_proxy) else None
            verification = await verify_group_comic_panels(
                images, [panel["scene"] for panel in story["panels"]], provider, proxy=proxy,
            )
            if verification.get("passed") is not True:
                raise ComicGenerationError("生成的画面未通过漫画内容检查")
            result = await render_group_comic(
                images, [panel["caption"] for panel in story["panels"]], directory,
                title=story["title"], source_note=(comic_source_time_note(source_window)
                    or f"取材：{story['source_topic'][:24]}；人物与情节为虚构。"),
            )
    except TimeoutError as exc:
        raise ComicGenerationError("漫画绘图超时") from exc
    plan = {
        "topics": normalize_comic_topics(topics),
        "selected_topics": [story["source_topic"]],
        "source_summary": story["source_summary"],
        "creation_intent": story["creation_intent"],
        "drawing_prompt": "\n\n".join(panel["drawing_prompt"] for panel in story["panels"]),
        "scene_fallback": False,
        "character_name": character_name,
        "render_mode": "illustrated_story",
        "render_quality": "visual_checked",
        "source_kind": "single_topic_adaptation",
        "source_note": story["source_note"],
        "panel_captions": [panel["caption"] for panel in story["panels"]],
        "panel_image_sha256": [hashlib.sha256(image).hexdigest() for image in images],
        "title": story["title"],
        "verification": verification,
        "source_window": dict(source_window) if isinstance(source_window, Mapping) else {},
    }
    return result, plan
