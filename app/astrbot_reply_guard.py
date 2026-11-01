"""群聊 LLM 发送前的短期避重，只缓存散列特征，不生成或补发回复。

在语音转换前调用 ``prepare_result``，收到原因字符串时本事件已清空并停止。
真实发送后的 hook 调用 ``mark_sent``；其他取消路径可调用 ``discard``。
命令、私聊、媒体及流式结果均保留原流程。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import unicodedata
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.astrbot_silence import (
    is_internal_silence_marker,
    is_silence_control_text,
    literal_silence_requested,
)

_LOGGER = logging.getLogger(__name__)
_CANDIDATE = "qqchat_reply_guard_candidate"
_SUPPRESSED = "qqchat_reply_guard_suppressed"
_PERMITTED = "qqchat_reply_guard_permitted_digest"
_MAX_TEXT = 24_000
_MAX_GRAMS = 256
_MAX_GROUPS = 64
_PER_GROUP = 24
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_REPEAT = re.compile(
    r"(?:再|重新)(?:发|发送|说|讲)(?:一遍|一次|一下|下|给我|给大家)|"
    r"(?:复述|重复)(?:一遍|一次|一下|下|刚才|上面|上一)|"
    r"重发(?:一遍|一次|一下|下|刚才|上面|上一)?|原样(?:再)?(?:发|说)|"
    r"(?:把|将).{0,40}(?:再发|重发|复述)|^(?:请|麻烦你?)?(?:再发|重新发|再说)[吧呗。！!？?]*$"
)
_NO_REPEAT = re.compile(
    r"(?:别|不要|不用|禁止|不准|不许|憋)[\s，,]*(?:再|又|重新)?(?:发|说|讲|复读|重复|复述|重发)|"
    r"什么意思|什么含义|为什么|为何|怎么老|总是|一直|又在"
)
_INTRO = re.compile(
    r"自我介绍|介绍(?:一下|下)?(?:你自己|自己)|你是谁|你叫什么|你是干什么的|"
    r"你(?:都)?(?:会|能)(?:做什么|干什么)|你有什么功能"
)
_FILLER_CLAUSES = tuple(re.compile(pattern) for pattern in (
    r"(?:哈哈|哈哈哈|嘿嘿|嘿|哦|嗯|嗨|你好|大家好|你们好|是你啊|哦是你啊)",
    r"(?:看来|看起来)?你对我的出现(?:挺|有点|很|非常|似乎有些)?惊讶(?:的)?(?:嘛|呢|啊)?",
    r"(?:作为|我是|我就是)?(?:一个)?(?:群聊|聊天|智能|ai|qq)?机器人(?:小丛雨|丛雨)?",
    (r"我(?:在这里)?(?:就是|主要)?(?:为了|来|负责|可以|会)?"
     r"(?:帮助大家|帮助你|为大家|帮大家)?"
     r"(?:解答问题|聊天互动|陪大家聊天|陪你聊天|聊天|提供帮助|回答问题|的|和|以及|与|互动)+"),
    (r"(?:如果|要是)?你(?:们)?(?:有任何疑问|有疑问|有问题|需要帮忙|需要帮助)"
     r"(?:(?:或者|或|以及)(?:有任何疑问|有疑问|有问题|需要帮忙|需要帮助))*"),
    r"随时(?:都)?(?:可以|欢迎)?(?:告诉我|问我|找我|来找我|来问我)(?:哦|呀|啊|呢|吧)?",
    r"(?:有什么|有啥)(?:我)?(?:可以|能)?(?:帮到你|帮你|帮助你|帮忙)(?:的)?(?:吗|么|嘛)?",
    r"(?:需要|有什么需要)(?:我)?(?:帮忙|帮助|帮你)(?:的)?(?:吗|么|嘛)?",
    r"我(?:可以|能)(?:为你|帮你)(?:做点什么|做些什么|做什么)(?:吗|呢)?",
))


def _hash(value: str) -> str:
    return hashlib.blake2b(value.encode("utf-8"), digest_size=16).hexdigest()


def _normalized(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", text).casefold()
                   if char.isalnum())


def _extra(event: Any, key: str, default: Any = None) -> Any:
    getter = getattr(event, "get_extra", None)
    return getter(key, default) if callable(getter) else default


def _set_extra(event: Any, key: str, value: Any) -> None:
    setter = getattr(event, "set_extra", None)
    if callable(setter):
        setter(key, value)


def _raw(event: Any) -> Mapping[str, Any]:
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    return raw if isinstance(raw, Mapping) else {}


def _prompt(event: Any) -> str:
    saved = _extra(event, "qqchat_group_current_human_text")
    if isinstance(saved, str):
        return saved
    message = _raw(event).get("message")
    if isinstance(message, list):
        parts = [str(part["data"].get("text", "")) for part in message
                 if isinstance(part, Mapping) and part.get("type") == "text"
                 and isinstance(part.get("data"), Mapping)]
        if parts:
            return "".join(parts)
    getter = getattr(event, "get_message_str", None)
    return str(_extra(event, "qqchat_original_message_str",
                      getter() if callable(getter) else "") or "")


def _group_key(event: Any) -> str | None:
    raw = _raw(event)
    getter = getattr(event, "get_platform_name", None)
    platform = str(getter() if callable(getter) else "").casefold()
    if not any(name in platform for name in ("qq", "aiocqhttp", "onebot")):
        return None
    if raw.get("message_type", "group") != "group":
        return None
    getter = getattr(event, "get_group_id", None)
    group = str(getter() if callable(getter) else raw.get("group_id") or "")
    getter = getattr(event, "get_self_id", None)
    self_id = str(getter() if callable(getter) else raw.get("self_id") or "")
    getter = getattr(event, "get_platform_id", None)
    platform_id = str(getter() if callable(getter) else platform)
    if not all((group, self_id, platform_id)):
        return None
    return _hash(json.dumps([platform_id, self_id, group], ensure_ascii=False))


def _is_command(event: Any) -> bool:
    if _extra(event, "qqchat_natural_command") or _prompt(event).lstrip().startswith("/"):
        return True
    for handler in _extra(event, "activated_handlers", ()) or ():
        for item in getattr(handler, "event_filters", ()) or ():
            if type(item).__name__ in {"CommandFilter", "CommandGroupFilter"}:
                return True
    return False


def _component_type(component: Any) -> str:
    kind = getattr(component, "type", None)
    value = getattr(kind, "value", kind)
    return str(value or type(component).__name__).rsplit(".", 1)[-1].casefold()


def _plain_llm_text(event: Any) -> str | None:
    result = event.get_result()
    checker = getattr(result, "is_llm_result", None)
    if not callable(checker) or not checker():
        return None
    chain = getattr(result, "chain", None)
    if not chain or any(_component_type(part) not in {"plain", "at", "reply"} for part in chain):
        return None
    return "\n".join(str(getattr(part, "text", "")) for part in chain
                     if _component_type(part) == "plain").strip() or None


def _boilerplate(text: str, prompt: str) -> bool:
    if _INTRO.search(prompt) or len(text) > 320:
        return False
    clauses = [_normalized(clause) for clause in re.split(r"[。？！!?；;，,\n]+", text)]
    clauses = [clause for clause in clauses if clause]
    if not clauses or not all(any(pattern.fullmatch(clause) for pattern in _FILLER_CLAUSES)
                              for clause in clauses):
        return False
    # 普通的“你好”“在的”不属于客服套话；必须含无信息的服务邀约或机器人自述。
    return bool(re.search(r"机器人|解答问题|帮到你|帮你|帮忙|做点什么|做些什么|做什么",
                          "".join(clauses)))


@dataclass(frozen=True)
class _Fingerprint:
    digest: str
    length: int
    grams: frozenset[str]
    numbers: str
    negations: int

    @classmethod
    def of(cls, text: str) -> _Fingerprint:
        normalized = _normalized(text)
        grams = {hashlib.blake2s(normalized[i:i + 3].encode("utf-8"), digest_size=6).hexdigest()
                 for i in range(max(0, len(normalized) - 2))}
        numbers = re.findall(r"\d+(?:[.,:/-]\d+)*", unicodedata.normalize("NFKC", text))
        return cls(_hash(normalized), len(normalized), frozenset(sorted(grams)[:_MAX_GRAMS]),
                   _hash(json.dumps(numbers)), len(re.findall(r"不|没|无|未|别|勿|否", normalized)))

    def resembles(self, other: _Fingerprint) -> bool:
        if self.digest == other.digest:
            # “42”“是”“在的”等短事实或确认可以合理地回答不同的新问题。
            # 无信息的客服套话由独立规则拦截，不需要扩大这条短答去重规则。
            return self.length >= 12
        if min(self.length, other.length) < 24 or self.numbers != other.numbers:
            return False
        if self.negations != other.negations:
            return False
        if min(self.length, other.length) / max(self.length, other.length) < 0.88:
            return False
        total = len(self.grams) + len(other.grams)
        return total > 0 and 2 * len(self.grams & other.grams) / total >= 0.90


@dataclass(frozen=True)
class _Candidate:
    group: str
    fingerprint: _Fingerprint
    at: float


class GroupReplyGuard:
    """只对群内纯文本 LLM 回复避重；拒发时返回 ``duplicate`` 或 ``boilerplate``。"""

    def __init__(
        self, cache_path: str | Path | None = None, *, ttl_seconds: float = 3600,
        pending_seconds: float = 120, clock: Callable[[], float] = time.time,
    ) -> None:
        self.cache_path = Path(cache_path) if cache_path is not None else None
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.pending_seconds = max(1.0, float(pending_seconds))
        self.clock = clock
        self._sent: OrderedDict[str, deque[_Candidate]] = OrderedDict()
        self._pending: OrderedDict[str, _Candidate] = OrderedDict()
        self._load()

    def _prune(self, now: float) -> None:
        for key, records in list(self._sent.items()):
            retained = deque((item for item in records if 0 <= now - item.at < self.ttl_seconds),
                             maxlen=_PER_GROUP)
            if retained:
                self._sent[key] = retained
            else:
                self._sent.pop(key)
        for token, item in list(self._pending.items()):
            if not 0 <= now - item.at < self.pending_seconds:
                self._pending.pop(token)

    def discard(self, event: Any) -> None:
        """释放未发出的占位，不把取消或发送失败的结果写入历史。"""
        token = _extra(event, _CANDIDATE)
        if isinstance(token, str):
            self._pending.pop(token, None)
        _set_extra(event, _CANDIDATE, None)

    def permit_once(self, event: Any, text: str) -> None:
        """仅为本轮已限频的诚实失败提示放行相同正文，不放行其他生成内容。"""
        _set_extra(event, _PERMITTED, _hash(_normalized(text)))

    def inspect_text(self, event: Any, text: str) -> str | None:
        """只判断正文，不清事件、不占位，供模型响应阶段的一次纠正使用。"""
        group = _group_key(event)
        if group is None or _is_command(event) or not text or len(text) > _MAX_TEXT:
            return None
        prompt = _prompt(event)
        if is_silence_control_text(text) and (
            is_internal_silence_marker(text)
            or _extra(event, "qqchat_group_reply_reason") == "interject"
            or not literal_silence_requested(prompt)
        ):
            return "silence_control"
        repeat_requested = bool(_REPEAT.search(prompt) and not _NO_REPEAT.search(prompt))
        now = self.clock()
        self._prune(now)
        fingerprint = _Fingerprint.of(text)
        if not fingerprint.length:
            return None
        if _extra(event, _PERMITTED) == fingerprint.digest:
            return None
        own_token = _extra(event, _CANDIDATE)
        records = (*self._sent.get(group, ()),
                   *(item for token, item in self._pending.items()
                     if item.group == group and token != own_token))
        reason = "boilerplate" if not repeat_requested and _boilerplate(text, prompt) else None
        if (not repeat_requested and reason is None
                and any(fingerprint.resembles(item.fingerprint) for item in records)):
            reason = "duplicate"
        return reason

    def prepare_result(self, event: Any) -> str | None:
        """在文字转语音前检查；允许发送返回 None，拒发则清结果并停止本事件。"""
        self.discard(event)  # 同一事件多次装饰不会与自己的占位发生重复判定。
        group = _group_key(event)
        if group is None or _is_command(event):
            return None
        text = _plain_llm_text(event)
        if text is None or len(text) > _MAX_TEXT:
            return None
        reason = self.inspect_text(event, text)
        if reason:
            _set_extra(event, _SUPPRESSED, reason)
            event.clear_result()
            event.stop_event()
            _LOGGER.info("群聊回复已抑制 group=%s reason=%s chars=%s", group[:12], reason, len(text))
            return reason
        fingerprint = _Fingerprint.of(text)
        if not fingerprint.length:
            return None
        token = uuid.uuid4().hex
        self._pending[token] = _Candidate(group, fingerprint, self.clock())
        while len(self._pending) > _MAX_GROUPS * _PER_GROUP:
            self._pending.popitem(last=False)
        _set_extra(event, _CANDIDATE, token)
        return None

    def mark_sent(self, event: Any) -> None:
        """从 after_message_sent 调用，仅记录适配器实际成功执行过发送的候选。"""
        token = _extra(event, _CANDIDATE)
        candidate = self._pending.get(token) if isinstance(token, str) else None
        self.discard(event)
        _set_extra(event, _PERMITTED, None)
        if candidate is None or not getattr(event, "_has_send_oper", False):
            return
        now = self.clock()
        self._prune(now)
        records = self._sent.setdefault(candidate.group, deque(maxlen=_PER_GROUP))
        records.append(_Candidate(candidate.group, candidate.fingerprint, now))
        self._sent.move_to_end(candidate.group)
        while len(self._sent) > _MAX_GROUPS:
            self._sent.popitem(last=False)
        self._persist()

    def _persist(self) -> None:
        if self.cache_path is None:
            return
        entries = [{"group": item.group, "at": item.at, "fingerprint": {
            "digest": item.fingerprint.digest, "length": item.fingerprint.length,
            "grams": sorted(item.fingerprint.grams), "numbers": item.fingerprint.numbers,
            "negations": item.fingerprint.negations,
        }} for records in self._sent.values() for item in records]
        temporary = self.cache_path.with_name(self.cache_path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({"version": 1, "entries": entries}, separators=(",", ":")),
                                 encoding="utf-8")
            os.replace(temporary, self.cache_path)
        except OSError as exc:
            _LOGGER.warning("群聊避重缓存保存失败（%s），继续使用内存缓存。", type(exc).__name__)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _load(self) -> None:
        if self.cache_path is None:
            return
        try:
            if not self.cache_path.is_file():
                return
            if self.cache_path.stat().st_size > _MAX_CACHE_BYTES:
                raise ValueError("cache too large")
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("unknown cache format")
            entries = payload.get("entries")
            if not isinstance(entries, list) or len(entries) > _MAX_GROUPS * _PER_GROUP:
                raise ValueError("invalid cache entries")
            now = self.clock()
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("fingerprint"), dict):
                    continue
                group, at, data = entry.get("group"), entry.get("at"), entry["fingerprint"]
                if not isinstance(group, str) or not re.fullmatch(r"[0-9a-f]{32}", group):
                    continue
                if not isinstance(at, (int, float)) or not 0 <= now - at < self.ttl_seconds:
                    continue
                grams = data.get("grams")
                if not isinstance(grams, list) or len(grams) > _MAX_GRAMS:
                    continue
                if any(not isinstance(gram, str) or not re.fullmatch(r"[0-9a-f]{12}", gram)
                       for gram in grams):
                    continue
                if not all(isinstance(data.get(key), str)
                           and re.fullmatch(r"[0-9a-f]{32}", data[key]) for key in ("digest", "numbers")):
                    continue
                if not isinstance(data.get("length"), int) or not 0 < data["length"] <= _MAX_TEXT:
                    continue
                if not isinstance(data.get("negations"), int) or not 0 <= data["negations"] <= _MAX_TEXT:
                    continue
                fingerprint = _Fingerprint(data["digest"], data["length"], frozenset(grams),
                                           data["numbers"], data["negations"])
                self._sent.setdefault(group, deque(maxlen=_PER_GROUP)).append(
                    _Candidate(group, fingerprint, at))
                self._sent.move_to_end(group)
                while len(self._sent) > _MAX_GROUPS:
                    self._sent.popitem(last=False)
        except (OSError, ValueError, TypeError) as exc:
            _LOGGER.warning("群聊避重缓存不可读取（%s），使用空缓存。", type(exc).__name__)
