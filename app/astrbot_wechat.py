"""把 AstrBot 微信消息接到旧 OneBot 聊天入口。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

_PRIVATE_GROUP_COMMANDS = frozenset(
    {
        "/bot on", "/bot off", "/机器人开启", "/机器人关闭",
        "/群记忆", "群记忆", "你在群里记住了什么",
        "/清除群记忆", "清除群记忆",
        "/清除成员身份", "清除成员身份", "/删除成员身份", "删除成员身份",
        "/重置好感度", "重置好感度",
        "/随机夺舍", "随机夺舍", "/今日夺舍", "今日夺舍", "今天夺舍谁", "今日附身",
        "/夺舍", "夺舍", "/指向夺舍", "指向夺舍", "指定夺舍",
        "/退出夺舍", "退出夺舍", "结束夺舍",
        "/今日奥特曼", "今日奥特曼", "抽奥特曼",
        "/随机二次元角色", "随机二次元角色", "/今日二次元角色", "今日二次元角色", "抽二次元角色",
    }
)

_PRIVATE_GROUP_PREFIXES = (
    "/blacklist ", "/黑名单 ",
    "删除群记忆", "清除群记忆", "删除记忆", "清除记忆",
    "群里记住：", "群里记住:", "记住，", "记住,", "记住 ", "记住我", "记住你",
    "让你记住我", "让你记住你",
)
_UNSUPPORTED_RECORD_TEXT = "微信个人号适配器不支持直接发送语音。"


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _call(obj: Any, name: str, default: Any = None) -> Any:
    method = _value(obj, name)
    if not callable(method):
        return default
    try:
        return method()
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return default


def _component_name(component: Any) -> str:
    name = type(component).__name__.casefold()
    if name not in {"dict", "simple_namespace"}:
        return name
    kind = _value(component, "type", "")
    return str(getattr(kind, "value", kind)).casefold()


def _platform_parts(event: Any) -> tuple[str, str, str]:
    platform = _value(event, "platform")
    meta = _value(event, "platform_meta") or platform
    name = str(_call(event, "get_platform_name", "") or _value(meta, "name", "")).strip()
    platform_id = str(_call(event, "get_platform_id", "") or _value(meta, "id", "") or "").strip()
    if not platform_id:
        metadata = _call(platform, "meta")
        platform_id = str(_value(metadata, "id", "") or "").strip()
    account_id = str(_value(platform, "account_id", "") or "").strip()
    if not account_id:
        account_id = platform_id
    return name.casefold(), platform_id, account_id


def _namespaced(kind: str, platform_id: str, account_id: str, scope: str, value: str) -> str:
    from urllib.parse import quote

    parts = (platform_id, account_id, scope, value)
    return "wechat:" + kind + ":" + ":".join(quote(str(part), safe="") for part in parts)


def _message_type(event: Any) -> str:
    kind = _call(event, "get_message_type")
    if kind is None:
        kind = _value(_value(event, "message_obj"), "type")
    if kind is None:
        kind = _value(_value(event, "session"), "message_type")
    kind = getattr(kind, "value", kind)
    text = str(kind or "").casefold()
    if "group" in text:
        return "group"
    group_id = _call(event, "get_group_id", "") or _value(_value(event, "message_obj"), "group_id", "")
    return "group" if group_id else "private"


def _component_location(component: Any) -> str:
    for field in ("url", "path", "file"):
        value = _value(component, field)
        if value:
            return str(value)
    return ""


def _onebot_segments(event: Any) -> list[dict[str, Any]]:
    components = _call(event, "get_messages")
    if components is None:
        components = _value(_value(event, "message_obj"), "message", [])
    if not isinstance(components, (list, tuple)):
        components = []

    plain_parts: list[str] = []
    segments: list[dict[str, Any]] = []
    has_record = False
    has_image = False
    for component in components:
        kind = _component_name(component)
        if kind in {"plain", "text"}:
            text = str(_value(component, "text", "") or "")
            if text:
                plain_parts.append(text)
        elif kind == "image":
            location = _component_location(component)
            data = {"file": location} if location else {}
            segments.append({"type": "image", "data": data})
            has_image = True
        elif kind == "record":
            location = _component_location(component)
            data = {"file": location} if location else {}
            segments.append({"type": "record", "data": data})
            has_record = True
        elif kind == "file":
            location = _component_location(component)
            name = str(_value(component, "name", "") or "")
            data = {"file": location}
            if name:
                data["name"] = name
            segments.append({"type": "file", "data": data})
        elif kind == "video":
            location = _component_location(component)
            segments.append({"type": "video", "data": {"file": location} if location else {}})
        elif kind == "at":
            user_id = _value(component, "qq", _value(component, "user_id", ""))
            if user_id:
                segments.append({"type": "at", "data": {"qq": str(user_id)}})

    text = "".join(plain_parts).strip()
    message_str = str(_call(event, "get_message_str", "") or "").strip()
    if has_record and message_str:
        transcript_parts = [
            part.strip()
            for part in message_str.splitlines()
            if part.strip() not in {"[语音]", "[音频]"}
        ]
        transcript = "\n".join(part for part in transcript_parts if part).strip()
        if transcript:
            text = transcript
        elif not plain_parts:
            text = ""
    elif not text and message_str:
        text = message_str
    if text:
        segments.insert(0, {"type": "text", "data": {"text": text}})
    elif has_image:
        segments.insert(0, {"type": "text", "data": {"text": "[图片]"}})
    return segments


def _private_group_only(text: str) -> bool:
    normalized = " ".join(text.split())
    folded = normalized.casefold()
    return normalized in _PRIVATE_GROUP_COMMANDS or folded.startswith(_PRIVATE_GROUP_PREFIXES)


def normalize_wechat_event(
    event: Any,
    *,
    account_id: str | None = None,
    platform_id: str | None = None,
) -> dict[str, Any] | None:
    """生成旧入口接受的事件；不支持的类型返回 None。"""
    platform_name, found_platform_id, found_account_id = _platform_parts(event)
    if not any(tag in platform_name for tag in ("weixin", "wechat", "wecom")):
        return None
    platform_id = str(platform_id or found_platform_id or platform_name).strip()
    account_id = str(account_id or found_account_id or platform_id).strip()
    if not platform_id or not account_id:
        return None

    kind = _message_type(event)
    sender_id = str(
        _call(event, "get_sender_id", "")
        or _value(_value(_value(event, "message_obj"), "sender"), "user_id", "")
        or ""
    ).strip()
    session_id = str(_call(event, "get_session_id", "") or sender_id).strip()
    if kind == "group":
        if not sender_id:
            return None
        peer_id = str(_call(event, "get_group_id", "") or _value(_value(event, "message_obj"), "group_id", "") or session_id).strip()
        scope = "group"
    else:
        sender_id = sender_id or session_id
        peer_id = sender_id or session_id
        scope = "private"
    if not peer_id or not sender_id:
        return None

    group_id = _namespaced("group", platform_id, account_id, scope, peer_id)
    user_id = _namespaced("user", platform_id, account_id, "member", sender_id)
    self_id = _namespaced("self", platform_id, account_id, "bot", platform_id)
    message = _onebot_segments(event)
    if kind == "private":
        text = next(
            (
                str(segment.get("data", {}).get("text", ""))
                for segment in message
                if segment.get("type") == "text"
            ),
            "",
        )
        if _private_group_only(text):
            return None
        message.insert(0, {"type": "at", "data": {"qq": self_id}})

    timestamp = _value(_value(event, "message_obj"), "timestamp", 0)
    try:
        timestamp = int(timestamp or 0)
    except (TypeError, ValueError):
        timestamp = 0
    message_id = str(
        _value(_value(event, "message_obj"), "message_id", "")
        or _value(event, "message_id", "")
        or ""
    ).strip()
    prefixed_message_id = _namespaced("msg", platform_id, account_id, "message", message_id) if message_id else ""
    nickname = str(_call(event, "get_sender_name", "") or sender_id)
    return {
        "time": timestamp,
        "self_id": self_id,
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": prefixed_message_id,
        "user_id": user_id,
        "group_id": group_id,
        "message": message,
        "raw_message": "",
        "sender": {"user_id": user_id, "nickname": nickname, "card": "", "role": "member"},
        "wechat_private": kind == "private",
    }


@dataclass(frozen=True)
class _Route:
    group_id: str
    user_id: str
    unified_msg_origin: str
    private: bool


@dataclass(frozen=True)
class _Components:
    MessageChain: Any
    Plain: Any
    Image: Any
    File: Any
    Video: Any


def _astrbot_components() -> _Components:
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import File, Image, Plain, Video

    return _Components(MessageChain, Plain, Image, File, Video)


def _local_path(value: str) -> Path | None:
    if value.startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path)
        if len(value) > 2 and value[0] == "/" and value[2] == ":":
            value = value[1:]
    if value.startswith(("http://", "https://", "base64://")):
        return None
    path = Path(value).expanduser()
    return path if path.is_file() else None


def _url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _failed(reason: str) -> dict[str, Any]:
    return {"status": "failed", "retcode": 1, "data": None, "wording": reason}


class WeChatTransport:
    """一个适配器账号共用的 OneBot action transport。"""

    def __init__(
        self,
        context: Any,
        *,
        platform_id: str,
        account_id: str | None = None,
        platform_name: str = "weixin_oc",
        max_routes: int = 512,
        component_types: _Components | None = None,
    ) -> None:
        self.context = context
        self.platform_id = str(platform_id or platform_name).strip()
        self.account_id = str(account_id or self.platform_id).strip()
        self._account_id_explicit = bool(account_id)
        self.platform_name = str(platform_name or "weixin_oc").strip()
        if not self.platform_id or not self.account_id:
            raise ValueError("platform_id and account_id must not be empty")
        self.max_routes = max(1, int(max_routes))
        self.component_types = component_types
        self._routes: OrderedDict[str, _Route] = OrderedDict()
        self._private_users: dict[str, str] = {}
        self.self_id = _namespaced("self", self.platform_id, self.account_id, "bot", self.platform_id)
        self.action_call = self._dispatch_action

    def bind_event(self, event: Any) -> dict[str, Any] | None:
        expected_platform = self.platform_name.casefold()
        found_platform, found_id, found_account = _platform_parts(event)
        if expected_platform not in found_platform and not any(
            tag in found_platform for tag in ("weixin", "wechat", "wecom")
        ):
            return None
        if found_id and found_id != self.platform_id:
            return None
        if self._account_id_explicit:
            if found_account and found_account != self.account_id:
                return None
        elif found_account:
            if self._routes and found_account != self.account_id:
                return None
            self.account_id = found_account
        normalized = normalize_wechat_event(
            event,
            account_id=self.account_id,
            platform_id=self.platform_id,
        )
        if normalized is None:
            return None
        self.self_id = normalized["self_id"]
        route = _Route(
            group_id=normalized["group_id"],
            user_id=normalized["user_id"],
            unified_msg_origin=str(_value(event, "unified_msg_origin", "") or ""),
            private=bool(normalized.get("wechat_private")),
        )
        if not route.unified_msg_origin:
            return None
        previous = self._routes.pop(route.group_id, None)
        if previous and previous.private and self._private_users.get(previous.user_id) == route.group_id:
            self._private_users.pop(previous.user_id, None)
        self._routes[route.group_id] = route
        if route.private:
            self._private_users[route.user_id] = route.group_id
        while len(self._routes) > self.max_routes:
            old_group_id, old_route = self._routes.popitem(last=False)
            if old_route.private and self._private_users.get(old_route.user_id) == old_group_id:
                self._private_users.pop(old_route.user_id, None)
        return normalized

    async def _dispatch_action(self, action: str, **params: Any) -> dict[str, Any]:
        route = self._resolve_route(action, params)
        if route is None:
            return _failed("目标会话无效或已过期。")
        components = self.component_types or _astrbot_components()
        message = params.get("message")
        if action in {"send_group_forward_msg", "send_private_forward_msg"}:
            message = self._flatten_forward(params)
        chain, warning = self._to_chain(message, components)
        if not chain.chain:
            return _failed("没有可发送的消息内容。")
        if warning:
            chain.chain.insert(0, components.Plain(warning))
        await self.context.send_message(route.unified_msg_origin, chain)
        return {"status": "ok", "retcode": 0, "data": {"message_id": None}}

    def _resolve_route(self, action: str, params: dict[str, Any]) -> _Route | None:
        if action not in {
            "send_msg", "send_group_msg", "send_private_msg",
            "send_group_forward_msg", "send_private_forward_msg",
        }:
            return None
        is_private_action = action.startswith("send_private_") or (
            action == "send_msg" and str(params.get("message_type", "")).casefold() == "private"
        )
        if is_private_action:
            user_id = str(params.get("user_id", "") or "").strip()
            group_id = self._private_users.get(user_id)
            route = self._routes.get(group_id or "")
            return route if route and route.private else None
        group_id = str(params.get("group_id", "") or "").strip()
        route = self._routes.get(group_id)
        return route if route else None

    @staticmethod
    def _flatten_forward(params: dict[str, Any]) -> list[Any]:
        nodes = params.get("messages", params.get("nodes", []))
        output: list[Any] = []
        if not isinstance(nodes, (list, tuple)):
            return output
        for node in nodes:
            data = _value(node, "data", {}) or {}
            content = _value(data, "content", _value(node, "content", []))
            if isinstance(content, (list, tuple)):
                output.extend(content)
            elif content:
                output.append(content)
        return output

    @staticmethod
    def _to_chain(message: Any, components: _Components) -> tuple[Any, str]:
        segments = message if isinstance(message, (list, tuple)) else [message]
        chain: list[Any] = []
        unsupported_record = False
        for segment in segments:
            if isinstance(segment, str):
                if segment:
                    chain.append(components.Plain(segment))
                continue
            if not isinstance(segment, dict):
                continue
            kind = str(segment.get("type", "")).casefold()
            data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
            if kind == "text":
                text = str(data.get("text", "") or "")
                if text:
                    chain.append(components.Plain(text))
            elif kind in {"image", "video", "file"}:
                location = str(data.get("url") or data.get("file") or data.get("path") or "")
                if not location:
                    continue
                if kind == "image" and location.startswith("base64://"):
                    item = components.Image.fromBase64(location.removeprefix("base64://"))
                elif _url(location):
                    item = components.Image.fromURL(location) if kind == "image" else (
                        components.Video.fromURL(location) if kind == "video" and hasattr(components.Video, "fromURL")
                        else components.File(name=str(data.get("name") or "附件"), url=location)
                    )
                else:
                    path = _local_path(location)
                    if path is None:
                        continue
                    item = components.Image.fromFileSystem(str(path)) if kind == "image" else (
                        components.Video.fromFileSystem(str(path)) if kind == "video" and hasattr(components.Video, "fromFileSystem")
                        else components.File(name=str(data.get("name") or path.name), file=str(path))
                    )
                chain.append(item)
            elif kind == "record":
                location = str(data.get("url") or data.get("file") or data.get("path") or "")
                if _url(location):
                    chain.append(components.File(name="翻唱音频", url=location))
                else:
                    path = _local_path(location)
                    if path is not None:
                        chain.append(components.File(name=path.name or "翻唱音频", file=str(path)))
                    else:
                        unsupported_record = True
            elif kind == "music":
                title = str(data.get("title") or "音乐")
                audio_url = str(data.get("audio") or "")
                if _url(audio_url):
                    chain.append(components.File(name=title, url=audio_url))
                else:
                    chain.append(components.Plain(title))
        if unsupported_record and chain:
            chain.insert(0, components.Plain(_UNSUPPORTED_RECORD_TEXT))
        return components.MessageChain(chain=chain), ""


__all__ = ["WeChatTransport", "normalize_wechat_event"]
