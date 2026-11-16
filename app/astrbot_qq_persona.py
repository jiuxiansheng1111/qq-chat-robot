"""QQ独立人格边界：清理受控的旧语气块，保留事实、工具与其他平台。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import copy
from pathlib import Path
from xml.parsers import expat

from app.astrbot_context_clean import _opening_end
from app.astrbot_identity import QQ_PERSONA_PATH

QQ_PERSONA_ID = "大肥鱼"
QQ_PLATFORMS = frozenset({"aiocqhttp", "onebot", "onebot_v11"})
_STYLE_TITLES = {
    "回复风格约束", "人格标准化：对话风格", "主动消息基础表达约束",
    # This mixed block also imposed "宁可短一点" and "亲密度和表达收一点".
    # QQ's source-labelled group context and verified owner adapter own identity
    # attribution now; Companion's target-user model is no longer authoritative.
    "群聊人格降噪",
}
_MARKER = "<!-- qqchat_dafeiyu_v1 -->"
_OLD_LENGTH_RULE = "普通闲聊单次回复不超过20个字符（含标点）；明确提问、求助，或明确要求解释、教程、总结等任务时，按需要完整回答，不用程序截断，也不为普通闲聊自行找理由超长。"


def is_qq(event) -> bool:
    return str(getattr(event, "get_platform_name", lambda: "")()).casefold() in QQ_PLATFORMS


def _without_style_sections(text: str) -> str:
    """Only remove well-formed, plugin-owned XML sections, never user/tool text."""
    raw = text.encode("utf-8")
    if len(raw) > 2 * 1024 * 1024 or b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        return text
    prefix = b"<managed>"
    data = prefix + raw + b"</managed>"
    parser = expat.ParserCreate()
    stack, spans = [], []

    def start(name, attrs):
        remove = name == "section" and attrs.get("title") in _STYLE_TITLES and not any(x[0] is not None for x in stack)
        begin = parser.CurrentByteIndex if remove else None
        finish = _opening_end(data, begin) if begin is not None else None
        empty_end = finish if finish and data[begin:finish].rstrip().endswith(b"/>") else None
        stack.append((begin, empty_end))

    def end(name):
        begin, empty_end = stack.pop()
        if begin is not None:
            finish = empty_end or data.index(b">", parser.CurrentByteIndex) + 1
            spans.append((begin - len(prefix), finish - len(prefix)))

    parser.StartElementHandler, parser.EndElementHandler = start, end
    try:
        parser.Parse(data, True)
    except (expat.ExpatError, ValueError, IndexError):
        return text
    for begin, finish in sorted(spans, reverse=True):
        raw = raw[:begin] + raw[finish:]
    return raw.decode("utf-8")


def apply_qq_persona(context, event, request, *, persona_path: Path = QQ_PERSONA_PATH) -> bool:
    """Run after context plugins; no changes to Bili/WeChat, user text or tool outputs."""
    if not is_qq(event):
        return False
    source = persona_path.read_text(encoding="utf-8").strip()
    system = str(getattr(request, "system_prompt", "") or "")
    if _MARKER in system:
        return False
    # Remove only the exact registered legacy base. Its Bili copy is untouched.
    manager = getattr(context, "persona_manager", None)
    for persona in getattr(manager, "personas_v3", ()) or ():
        if persona.get("name") in {"qqchat-local-persona", QQ_PERSONA_ID}:
            base = str(persona.get("prompt", "") or "").strip()
            if base:
                system = system.replace(base, "")
    system = system.replace(_OLD_LENGTH_RULE, "闲聊长度按当前QQ人格执行；详细任务完整回答。")
    # Companion emits these sections into a dedicated managed text part.
    parts = getattr(request, "extra_user_content_parts", None)
    if isinstance(parts, (list, tuple)):
        rewritten = list(parts)
        for index, part in enumerate(parts):
            def field(key, default=None, value=part):
                return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)
            if field("_private_companion_turn_fragments") is not True or field("type", "text") != "text":
                continue
            text = field("text", "")
            if not isinstance(text, str):
                continue
            cleaned = _without_style_sections(text)
            if cleaned != text:
                replacement = dict(part) if isinstance(part, Mapping) else copy(part)
                if isinstance(replacement, dict):
                    replacement["text"] = cleaned
                else:
                    replacement.text = cleaned
                rewritten[index] = replacement
        request.extra_user_content_parts = rewritten
    # Fallback placements keep a separate exact owned string. Clean that string
    # rather than parsing the surrounding user/system text as XML.
    for attr, destination in (("_private_companion_conversation_plan_system_text", "system"),
                              ("_private_companion_conversation_plan_turn_text", "prompt")):
        owned = getattr(request, attr, "")
        if not isinstance(owned, str) or not owned:
            continue
        cleaned = _without_style_sections(owned)
        if cleaned == owned:
            continue
        if destination == "system":
            system = system.replace(owned, cleaned, 1)
        elif not any((p.get("_private_companion_turn_fragments") if isinstance(p, Mapping) else getattr(p, "_private_companion_turn_fragments", False)) for p in (parts or ())):
            request.prompt = str(getattr(request, "prompt", "") or "").replace(owned, cleaned, 1)
    request.system_prompt = system.strip() + "\n\n" + _MARKER + "\n" + source
    return True
