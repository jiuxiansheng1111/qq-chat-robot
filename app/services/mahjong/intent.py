"""Narrow explicit review intent and short-lived, same-owner attachment continuation."""

import re
import time

NEGATIVE = re.compile(r"不要|别|不用|无需|不必|不想|先不|暂时不")
REQUEST = re.compile(
    r"(?:请(?:你)?[，,:：]*)?(?:帮我|帮忙|给我|替我|麻烦(?:你)?(?:帮我)?)?"
    r"(?:分析|复盘)(?:一下|下)?(?:这(?:个|份|局|场)?)?(?:雀魂)?(?:四人)?(?:麻将)?"
    r"(?:牌谱|对局|牌局)(?:吧|呀|啊|呗)?[。！!？?，,:：]*"
)
PARAMETER = re.compile(r"(?:^|\s)(座位|赛制|文件|链接|AI)\s*[=＝]\s*([^\s]+)")


def normalize_intent(text: str) -> str | None:
    text = str(text).strip()
    if NEGATIVE.search(text):
        return None
    if text.startswith(("/牌谱分析", "牌谱分析", "/雀魂复盘", "雀魂复盘")):
        prefix = next(
            p for p in ("/牌谱分析", "牌谱分析", "/雀魂复盘", "雀魂复盘") if text.startswith(p)
        )
        tail = text[len(prefix) :]
        if tail and not tail[0].isspace() and tail[0] not in "：:，,。！!":
            return None
        return "/牌谱分析" + (
            " " + tail.lstrip(" ：:，,。！!") if tail.strip(" ：:，,。！!") else ""
        )
    marker = re.search(r"(?:座位|赛制|文件|链接|JSON|AI)\s*[=＝]|https://", text)
    phrase = text[: marker.start()] if marker else text
    tail = text[marker.start() :] if marker else ""
    compact = re.sub(r"\s+", "", phrase).lstrip("@")
    if not REQUEST.fullmatch(compact):
        return None
    if tail.startswith("https://"):
        tail = "链接=" + tail
    return "/牌谱分析" + (" " + tail if tail else "")


def owner_key(event: dict) -> tuple[str, str, str]:
    return (
        str(event.get("self_id", "")),
        str(event.get("group_id", "")),
        str(event.get("user_id", "")),
    )


def pending_requests(state) -> dict:
    if not hasattr(state, "mahjong_pending"):
        state.mahjong_pending = {}
    now = time.monotonic()
    for key, (deadline, _) in list(state.mahjong_pending.items()):
        if deadline <= now:
            state.mahjong_pending.pop(key, None)
    return state.mahjong_pending


def attachment_name(event: dict) -> str | None:
    message = event.get("message")
    if not isinstance(message, list):
        return None
    found = []
    for segment in message:
        if not isinstance(segment, dict) or segment.get("type") != "file":
            continue
        data = segment.get("data", {})
        if not isinstance(data, dict):
            continue
        # URL/path/file_id are deliberately never interpreted as trusted local bytes.
        name = data.get("name") or data.get("file_name")
        if isinstance(name, str) and re.fullmatch(
            r"[\w.-]{1,100}\.(?:json|jsonl|mjson)", name, re.IGNORECASE
        ):
            found.append(name)
    return found[0] if len(found) == 1 else None


def prepare_event_text(event: dict, text: str, state) -> str:
    """Called inside the original group dispatch. All existing ACL/rate-limit gates follow."""
    normalized = normalize_intent(text)
    pending = pending_requests(state)
    key = owner_key(event)
    if NEGATIVE.search(text) and ("牌谱" in text or "对局" in text):
        pending.pop(key, None)
        return text
    file_name = attachment_name(event)
    continuation = key in pending and (file_name is not None or bool(PARAMETER.search(text)))
    if normalized is None and not continuation:
        return text
    if normalized is None:
        normalized = "/牌谱分析" + (" " + text.strip() if text.strip() else "")
    if file_name and not re.search(r"(?:文件|链接|JSON)\s*[=＝]", normalized):
        normalized += " 文件=" + file_name
    return normalized


def complete_request(state, event: dict, args: str) -> tuple[str | None, str | None]:
    """Store only input metadata for 5 minutes, never hidden hands or account credentials."""
    pending = pending_requests(state)
    key = owner_key(event)
    _, old = pending.get(key, (0, {}))
    header, marker, body = args.partition(" JSON=")
    pairs = PARAMETER.findall(header)
    if len({k for k, _ in pairs}) != len(pairs):
        return None, "参数重复，请只提供一个座位、赛制和输入。"
    merged = {**old, **dict(pairs)}
    if marker:
        merged.pop("文件", None)
        merged.pop("链接", None)
        merged["JSON"] = body
    elif "文件" in dict(pairs) or "链接" in dict(pairs):
        merged.pop("JSON", None)
    missing = []
    if "座位" not in merged:
        missing.append("座位=东/南/西/北（开局固定座位）")
    if "赛制" not in merged:
        missing.append("赛制=东风/半庄")
    if not any(k in merged for k in ("文件", "链接", "JSON")):
        missing.append("已导出的JSON/JSONL文件，或官方牌谱链接")
    if missing:
        if len(pending) >= 64 and key not in pending:
            pending.pop(next(iter(pending)))
        # Do not retain a full inline replay while awaiting metadata.
        stored = {k: v for k, v in merged.items() if k != "JSON"}
        pending[key] = (time.monotonic() + 300, stored)
        return None, (
            "可以，本地文字复盘还需要：" + "；".join(missing) + "。\n"
            "请在5分钟内由同一用户、同一会话补充。附件需已由可信本地流程放入data/mahjong/inbox；"
            "不会下载消息中任意URL。官方链接当前仅识别，未实现原生抓取解码。"
        )
    pending.pop(key, None)
    prepared = " ".join(f"{k}={v}" for k, v in merged.items() if k != "JSON")
    if "JSON" in merged:
        prepared += " JSON=" + merged["JSON"]
    return prepared, None
