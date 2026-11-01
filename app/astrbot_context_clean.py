"""仅清理本轮受控群聊 XML 中的旧 Bot 节点，不改观察缓存或引用证据。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import copy
from xml.parsers import expat

_MARKER = "<!-- private_companion_group_context_v1 -->"
_MANAGED_PART = "_private_companion_turn_fragments"
_FRAGMENTS = "_private_companion_turn_prompt_fragments"
_PLAN = "_private_companion_conversation_injection_plan"


def _field(value: object, key: str, default: object = None) -> object:
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _has_group_marker(request: object, text: str) -> bool:
    if _MARKER in text:
        return True
    fragments = getattr(request, _FRAGMENTS, None)
    if isinstance(fragments, (list, tuple)) and any(
        _field(fragment, "marker") == _MARKER for fragment in fragments
    ):
        return True
    blocks = getattr(getattr(request, _PLAN, None), "blocks", None)
    if not callable(blocks):
        return False
    try:
        for block in blocks():
            if _field(block, "key") != "group.context":
                continue
            metadata = _field(block, "metadata", {})
            if _field(block, "marker") == _MARKER or (
                isinstance(metadata, Mapping) and metadata.get("delivery_group_marker") == _MARKER
            ):
                return True
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return False
    return False


def _opening_end(data: bytes, start: int) -> int:
    """跳过属性引号里的大于号，定位 XML 起始标签末尾。"""
    quote = 0
    for index in range(start, len(data)):
        value = data[index]
        if quote:
            if value == quote:
                quote = 0
        elif value in (34, 39):
            quote = value
        elif value == 62:
            return index + 1
    raise ValueError("XML起始标签不完整")


def _without_bot_history(text: str) -> tuple[str, int]:
    """验证整段 XML 后只删精确路径的节点，保留其它文字和格式。"""
    raw = text.encode("utf-8")
    if len(raw) > 2 * 1024 * 1024 or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        return text, 0
    prefix = b"<managed>"
    data = prefix + raw + b"</managed>"
    parser = expat.ParserCreate()
    stack: list[tuple[str, dict[str, str], int | None, int | None]] = []
    spans: list[tuple[int, int]] = []

    def start(name: str, attrs: dict[str, str]) -> None:
        remove = (
            name == "message"
            and attrs.get("role") == "assistant"
            and len(stack) == 4
            and [frame[0] for frame in stack] == ["managed", "section", "group_context", "history"]
            and stack[1][1].get("title") == "群聊上下文"
        )
        begin = parser.CurrentByteIndex if remove else None
        empty_end = None
        if begin is not None:
            end = _opening_end(data, begin)
            if data[begin:end].rstrip().endswith(b"/>"):
                empty_end = end
        stack.append((name, attrs, begin, empty_end))

    def end(_name: str) -> None:
        _, _, begin, empty_end = stack.pop()
        if begin is not None:
            finish = (
                empty_end
                if empty_end is not None
                else data.index(b">", parser.CurrentByteIndex) + 1
            )
            spans.append((begin - len(prefix), finish - len(prefix)))

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
    except (expat.ExpatError, ValueError, IndexError):
        return text, 0
    for begin, finish in reversed(spans):
        raw = raw[:begin] + raw[finish:]
    return raw.decode("utf-8"), len(spans)


def clean_managed_group_bot_history(request: object) -> int:
    """只替换当前请求的受控文本 part，不修改原 part、注入计划或持久缓存。

    Args:
        request: AstrBot 本轮 ProviderRequest。

    Returns:
        实际从请求视图删除的旧 Bot 历史节点数。
    """
    parts = getattr(request, "extra_user_content_parts", None)
    if not isinstance(parts, (list, tuple)):
        return 0
    result = list(parts)
    removed = 0
    for index, part in enumerate(parts):
        text = _field(part, "text")
        if (
            _field(part, _MANAGED_PART) is not True
            or _field(part, "type", "text") != "text"
            or not isinstance(text, str)
            or not _has_group_marker(request, text)
        ):
            continue
        cleaned, count = _without_bot_history(text)
        if not count:
            continue
        if isinstance(part, Mapping):
            replacement = {**part, "text": cleaned}
        else:
            try:
                replacement = copy(part)
                if replacement is part:
                    continue
                replacement.text = cleaned
            except (AttributeError, TypeError, ValueError):
                continue
        result[index] = replacement
        removed += count
    if removed:
        request.extra_user_content_parts = result
    return removed
