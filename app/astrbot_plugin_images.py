"""把明确的随机二次元图片请求转给已启用的 AstrBot 图片插件。"""

from __future__ import annotations

import re
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

IMAGE_PLUGIN_NAME = "astrbot_plugin_get_px"
_LOGGER = logging.getLogger(__name__)

_SUBJECT = r"(?:二次元|动漫|动画)(?:图片|图|插画|壁纸)"
_REQUEST_LEAD = r"(?:(?:请|麻烦)?(?:帮我|给我|我想要|我想看|我想来))"
_LEAD = rf"(?:{_REQUEST_LEAD}|请)"
_OPTIONAL_LEAD = rf"(?:{_LEAD})?"
_COUNT = r"(?:一张|张|一幅|一份|份|一个|个|一)?"
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


def _is_general_image_request(text: str) -> bool:
    """识别短的发图意图，不接管抽角色、图鉴或讨论句。"""
    if not isinstance(text, str):
        return False
    compact = re.sub(r"\s+", "", text).strip().removeprefix("/").rstrip(_TRAILING_PUNCTUATION)
    for ending in _TRAILING_POLITENESS:
        if compact.endswith(ending):
            compact = compact[: -len(ending)]
            break
    if not compact or len(compact) > 24:
        return False
    if any(word in compact for word in _NEGATIVE_OR_DISCUSSION):
        return False
    return any(pattern.fullmatch(compact) for pattern in _IMAGE_REQUEST_PATTERNS)


@dataclass(frozen=True)
class AnimeImageRequest:
    styled: bool = False
    themes: tuple[str, ...] = ()
    blocked: bool = False


_STYLE_THEMES = {
    "泳装": ("泳装", "泳衣", "比基尼"),
    "短裙": ("短裙",),
    "丝袜": ("丝袜", "黑丝", "白丝", "长筒袜"),
    "女仆": ("女仆", "メイド"),
}
_STYLE_LEAD = re.compile(
    r"^(?:(?:请|麻烦)?(?:帮我|给我)?(?:来|发|整|弄|找|求)(?:一张|一份|一幅|张|份|幅|点|个|一)?"
    r"|(?:我想看|我想要|想看|求)(?:一张|一份|张|份)?)"
)
_STYLE_WORDS = ("色图", "涩图", "瑟图", "福利图", "擦边", "性感")


def parse_anime_image_request(text: str) -> AnimeImageRequest | None:
    if not isinstance(text, str):
        return None
    compact = re.sub(r"\s+", "", text).strip().lstrip("/／").rstrip(_TRAILING_PUNCTUATION)
    # A negative content constraint is not a negated request. Never forward it as a search tag.
    compact = re.sub(r"(?:不要|不含|非|不带)[Rr][-－]?18|不要露点|不露点", "", compact)
    compact = compact.rstrip(_TRAILING_PUNCTUATION)
    if not compact or len(compact) > 48 or any(word in compact for word in _NEGATIVE_OR_DISCUSSION):
        return None
    if _STYLE_LEAD.match(compact):
        themes = tuple(name for name, aliases in _STYLE_THEMES.items() if any(a in compact for a in aliases))
        if themes or any(word in compact for word in _STYLE_WORDS):
            from app.services.anime_image_policy import blocked_image_text
            blocked = blocked_image_text(compact)
            if blocked:
                return AnimeImageRequest(styled=True, themes=themes, blocked=True)
            body = _STYLE_LEAD.sub("", compact, count=1)
            allowed_words = (
                "二次元", "动漫", "成年", "角色", "小姐姐", "稍微", "有点", "比较", "一点", "一些", "清凉", "好看",
                "随机", "随便", "图片", "插画", "壁纸", "图", "色", "涩", "瑟", "福利", "擦边", "性感",
                "的", "吧", "呗", "呀", "啊", "谢谢", "一下", *[a for values in _STYLE_THEMES.values() for a in values],
            )
            body = re.sub("|".join(re.escape(word) for word in sorted(allowed_words, key=len, reverse=True)), "", body)
            if not body:
                return AnimeImageRequest(styled=True, themes=themes)
    if _is_general_image_request(text):
        return AnimeImageRequest()
    return None


def is_anime_image_request(text: str) -> bool:
    """短发图请求；题材请求另走成年人、非露骨视觉筛选。"""
    return parse_anime_image_request(text) is not None


async def forward_anime_image(event: Any, context: Any) -> AsyncIterator[Any] | None:
    """返回图片插件的随机发图迭代器；未命中或插件不可用时返回 None。"""
    get_message_str = getattr(event, "get_message_str", None)
    if not callable(get_message_str) or (request := parse_anime_image_request(get_message_str())) is None:
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
    plugin = getattr(registered, "star_cls", registered)
    handler = getattr(plugin, "cmd_p", None)
    if not callable(handler):
        return None

    from app.astrbot_menu import _active_commands

    # 短句也要遵守面板中的平台、插件范围和指令权限。
    if not any(
        item["plugin_name"] == IMAGE_PLUGIN_NAME
        and item.get("handler_name") == "cmd_p"
        for item in _active_commands(context, event)
    ):
        return None

    if request.styled:
        # Only the existing QQ group route gets the new colloquial aliases.
        if not callable(getattr(event, "get_group_id", None)) or not event.get_group_id():
            return None
        if not callable(getattr(event, "get_platform_name", None)) or event.get_platform_name() != "aiocqhttp":
            return None
        from app.services.anime_image_policy import send_styled_anime_image
        _LOGGER.info("QQ题材插画路由命中：%s", "/".join(request.themes) or "随机题材")
        return send_styled_anime_image(event, context, plugin, request)

    result = handler(event, query="")
    if not hasattr(result, "__aiter__"):
        return None
    return result
