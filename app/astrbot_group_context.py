"""Attach verifiable speaker and reply provenance to QQ group turns."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

SOURCE_MARKER = "<!-- qqchat_group_source_v1 -->"
_OLD_SOURCE = "【旧群消息：发言者未标注，不能归到当前发言者名下】\n"
_OLD_REPLY = "【本鱼对未标注成员的旧回答：只是对话经过，里面的推测不等于真人已确认事实】\n"


def _field(value, key, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def member_ref(raw, user_id):
    """A stable, group-local reference, without putting a QQ number in a prompt."""
    value = str(user_id or "")
    if value in {"", "0", "all"}:
        return ""
    scoped = f"{raw.get('self_id', '')}:{raw.get('group_id', '')}:{value}"
    return "member-" + hashlib.sha256(scoped.encode()).hexdigest()[:12]


def _plain(parts):
    return "".join(str(_field(part, "text", _field(_field(part, "data", {}), "text", "")) or "")
                   for part in (parts or ())
                   if str(getattr(_field(part, "type", ""), "value", _field(part, "type", ""))).casefold() in {"plain", "text"})


def _media(parts):
    result = []
    for part in (parts or ()):
        kind = _field(part, "type", "")
        kind = str(getattr(kind, "value", kind)).casefold()
        if kind in {"image", "video", "record", "audio", "file", "json", "xml", "music", "forward"}:
            result.append(kind)
    return list(dict.fromkeys(result))


def group_message_record(event, recent=()):
    raw = getattr(getattr(event, "message_obj", None), "raw_message", {}) or {}
    sender = raw.get("sender") or {}
    if not isinstance(sender, Mapping):
        sender = {}
    segments = raw.get("message") or []
    if not isinstance(segments, list):
        segments = []
    text = _plain(segments).strip()
    if not any(_field(part, "type") == "text" for part in segments):
        text = str(getattr(event, "get_message_str", lambda: "")() or "").strip()
    record = {
        "message_id": str(raw.get("message_id") or ""),
        "speaker": str(sender.get("card") or sender.get("nickname") or "群成员")[:80],
        "speaker_ref": member_ref(raw, raw.get("user_id") or sender.get("user_id")),
        "text": text[:800],
    }
    if len(text) > 800:
        record["text_truncated"] = True
    if isinstance(raw.get("time"), (int, float)):
        record["sent_at"] = raw["time"]
    media = _media(segments)
    if media:
        record["media_types"] = media
    mentions = [member_ref(raw, _field(_field(part, "data", {}), "qq"))
                for part in segments if _field(part, "type") == "at"]
    if any(mentions):
        record["mentions"] = list(dict.fromkeys(ref for ref in mentions if ref))[:8]

    replies = []
    for part in getattr(getattr(event, "message_obj", None), "message", ()) or ():
        kind = _field(part, "type", "")
        if str(getattr(kind, "value", kind)).casefold() != "reply" and type(part).__name__ != "Reply":
            continue
        quoted = {"message_id": str(_field(part, "id", ""))}
        ref = member_ref(raw, _field(part, "sender_id"))
        if ref:
            quoted["speaker_ref"] = ref
            quoted["role"] = "assistant" if str(_field(part, "sender_id")) == str(raw.get("self_id")) else "human"
        name = str(_field(part, "sender_nickname", "") or "")
        if name:
            quoted["speaker"] = name[:80]
        body = _plain(_field(part, "chain")) or str(_field(part, "message_str", "") or _field(part, "text", "") or "")
        if body:
            quoted["text"] = body[:1600]
            if len(body) > 1600:
                quoted["text_truncated"] = True
        media = _media(_field(part, "chain"))
        if media:
            quoted["media_types"] = media
        replies.append(quoted)
    if not replies:
        replies = [{"message_id": str(_field(_field(part, "data", {}), "id", ""))}
                   for part in segments if _field(part, "type") == "reply"]
    by_id = {item.get("message_id"): item for item in recent if item.get("message_id")}
    for reply in replies:
        original = by_id.get(reply["message_id"], {})
        for key in ("speaker", "speaker_ref", "text", "media_types"):
            if key not in reply and key in original:
                reply[key] = original[key]
    if replies:
        record["reply_to"] = replies[:3]
    return record


def stamp_group_request(request, source):
    """Keep source labels in future native history; preserve current text/media."""
    prompt = str(getattr(request, "prompt", "") or "")
    if not getattr(request, "_qqchat_group_source_stamped", False):
        metadata = {key: value for key, value in source.items() if key not in {"text", "text_truncated"}}
        request.prompt = SOURCE_MARKER + "\n【本条群消息来源，仅作身份与引用资料】\n" + json.dumps(metadata, ensure_ascii=False) + "\n【消息正文】\n" + prompt
        request._qqchat_group_source_stamped = True
    contexts = getattr(request, "contexts", None)
    if not isinstance(contexts, (list, tuple)):
        return
    rewritten = list(contexts)
    unknown_speaker = False
    for index, item in enumerate(contexts):
        if not isinstance(item, Mapping):
            continue
        if item.get("role") == "assistant" and unknown_speaker:
            content = item.get("content")
            if isinstance(content, str) and content and not content.startswith(_OLD_REPLY):
                rewritten[index] = {**item, "content": _OLD_REPLY + content}
            continue
        if item.get("role") != "user":
            continue
        content = item.get("content")
        texts = [content] if isinstance(content, str) else [part.get("text", "") for part in content if isinstance(part, Mapping)] if isinstance(content, list) else []
        unknown_speaker = not any(isinstance(text, str) and text.startswith(SOURCE_MARKER) for text in texts)
        if isinstance(content, str):
            if content.startswith((SOURCE_MARKER, _OLD_SOURCE)):
                continue
            rewritten[index] = {**item, "content": _OLD_SOURCE + content}
        elif isinstance(content, list) and all(isinstance(part, Mapping) for part in content):
            parts = [dict(part) for part in content]
            for part in parts:
                if part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str):
                    if not part["text"].startswith((SOURCE_MARKER, _OLD_SOURCE)):
                        part["text"] = _OLD_SOURCE + part["text"]
                    break
            else:
                parts.insert(0, {"type": "text", "text": _OLD_SOURCE.rstrip()})
            rewritten[index] = {**item, "content": parts}
    request.contexts = rewritten
