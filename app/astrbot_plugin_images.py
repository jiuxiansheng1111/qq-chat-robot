"""把明确的随机二次元图片请求转给已启用的 AstrBot 图片插件。"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

IMAGE_PLUGIN_NAME = "astrbot_plugin_get_px"

_SUBJECT = r"(?:二次元|动漫|动画)(?:图片|图|插画|壁纸)"
_REQUEST_LEAD = r"(?:(?:请|麻烦)?(?:帮我|给我|我想要|我想看|我想来))"
_LEAD = rf"(?:{_REQUEST_LEAD}|请)"
_OPTIONAL_LEAD = rf"(?:{_LEAD})?"
_COUNT = r"(?:一张|张|一幅|一个|个|一)?"
_RANDOM = r"(?:随机|随便)?"
_REQUIRED_RANDOM = r"(?:随机|随便)"
_ACTION = r"(?:来|发|找|求|整|弄|看)"
_IMAGE_REQUEST_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        _OPTIONAL_LEAD + _REQUIRED_RANDOM + _SUBJECT,
        _REQUEST_LEAD + _SUBJECT,
        _OPTIONAL_LEAD + _ACTION + _COUNT + _RANDOM + _SUBJECT,
        _OPTIONAL_LEAD + _RANDOM + _ACTION + _COUNT + _SUBJECT,
        _OPTIONAL_LEAD + _ACTION + _RANDOM + _COUNT + _SUBJECT,
        _LEAD + _COUNT + _RANDOM + _SUBJECT,
        _LEAD + _RANDOM + _COUNT + _SUBJECT,
    )
)
_TRAILING_PUNCTUATION = " \t\r\n，。！？!?,.;；：:~～"
_TRAILING_POLITENESS = ("谢谢", "一下", "吧", "呗", "呀", "啊")
_NEGATIVE_OR_DISCUSSION = ("不要", "别", "为什么", "怎么", "如何", "是否", "是什么")


def is_anime_image_request(text: str) -> bool:
    """识别短的发图意图，不接管抽角色、图鉴或讨论句。"""
    if not isinstance(text, str):
        return False
    compact = re.sub(r"\s+", "", text).strip().rstrip(_TRAILING_PUNCTUATION)
    for ending in _TRAILING_POLITENESS:
        if compact.endswith(ending):
            compact = compact[: -len(ending)]
            break
    if not compact or len(compact) > 24:
        return False
    if any(word in compact for word in _NEGATIVE_OR_DISCUSSION):
        return False
    return any(pattern.fullmatch(compact) for pattern in _IMAGE_REQUEST_PATTERNS)


async def forward_anime_image(event: Any, context: Any) -> AsyncIterator[Any] | None:
    """返回图片插件的随机发图迭代器；未命中或插件不可用时返回 None。"""
    get_message_str = getattr(event, "get_message_str", None)
    if not callable(get_message_str) or not is_anime_image_request(get_message_str()):
        return None

    get_registered_star = getattr(context, "get_registered_star", None)
    if not callable(get_registered_star):
        return None
    try:
        registered = get_registered_star(IMAGE_PLUGIN_NAME)
    except (AttributeError, LookupError, RuntimeError, TypeError, ValueError):
        return None
    if registered is None or not getattr(registered, "activated", True):
        return None

    from app.astrbot_menu import _active_commands

    # 短句也要遵守面板中的平台、插件范围和指令权限。
    if not any(
        item["plugin_name"] == IMAGE_PLUGIN_NAME
        and item.get("handler_name") == "cmd_p"
        for item in _active_commands(context, event)
    ):
        return None

    # AstrBot 4.28.2 返回 StarMetadata，star_cls 是已实例化插件。
    plugin = getattr(registered, "star_cls", registered)
    handler = getattr(plugin, "cmd_p", None)
    if not callable(handler):
        return None

    result = handler(event, query="")
    if not hasattr(result, "__aiter__"):
        return None
    return result
