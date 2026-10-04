"""列出当前会话可用的 AstrBot 命令并组装统一菜单。"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

_NATIVE_COMMANDS = {
    "help": "帮助菜单",
    "sid": "查看会话信息",
    "stop": "停止当前生成",
    "stats": "原生会话用量（不含旧业务调用）",
    "reset": "清除当前短上下文",
    "new": "开启新对话",
    "provider": "切换当前会话模型",
}
_NATIVE_ORDER = ("help", "sid", "stop", "stats", "reset", "new", "provider")
_MENU_WORDS = ("帮助", "菜单", "功能", "指令", "命令", "help", "menu")
_REQUEST_WORDS = (
    "给我看看", "让我看看", "帮我看看", "看看", "看一下", "查看", "想看", "要看",
    "发给我", "发我", "发一份", "发一下", "发下", "显示", "列一下", "列出",
    "给我", "我要", "告诉我", "怎么用", "你会什么", "能干嘛", "能做什么",
)
_EXPLICIT_HELP_REQUESTS = frozenset({
    "你会什么", "你能干嘛", "能干嘛", "有什么功能", "有哪些功能",
    "功能有哪些", "有什么指令", "有哪些指令",
})
_NEGATION = re.compile(r"(?:不要|别|不想|不需要|不用|无需|先别|先不|不发|不看)")
_TEXT_MENU_NAMES = frozenset({
    "文字版菜单", "文字菜单", "文字版帮助", "菜单文字版", "插件菜单", "插件命令",
    "文字版功能", "纯文字菜单",
})
_LOGGER = logging.getLogger(__name__)


def menu_request_kind(text: str) -> str | None:
    """只识别短而明确的菜单请求。"""
    if not isinstance(text, str):
        return None
    value = " ".join(text.strip().split())
    if value.startswith("/"):
        value = value[1:].strip()
    normalized = re.sub(r"\s+", "", value).casefold()
    if normalized in {name.casefold() for name in _TEXT_MENU_NAMES}:
        return "text"
    if len(value) > 36 or _NEGATION.search(normalized):
        return None
    explicit_request = any(
        word in normalized
        for word in ("发", "给我", "我要", "来一份", "来个", "看看", "显示")
    )
    if explicit_request and any(name in normalized for name in _TEXT_MENU_NAMES):
        return "text"
    if normalized in {"help", "帮助", "菜单", "功能菜单", "功能", "指令", "命令", "menu"}:
        return "image"
    if any(request in normalized for request in _EXPLICIT_HELP_REQUESTS):
        return "image"
    if any(word.casefold() in normalized for word in _MENU_WORDS) and any(
        word.casefold() in normalized for word in _REQUEST_WORDS
    ):
        return "image"
    return None


def is_native_reset_or_new(text: str) -> bool:
    """识别原始文本中的 /reset 和 /new，不匹配普通提及。"""
    if not isinstance(text, str):
        return False
    value = " ".join(text.strip().split()).casefold()
    if not value.startswith("/"):
        return False
    return value[1:] in {"reset", "new"}


def native_reset_command(context: Any, event: Any, *texts: str) -> str | None:
    """按注册名称和原始功能 ID 识别已改名的 /reset、/new。"""
    if not getattr(event, "is_at_or_wake_command", False):
        return None
    inputs = {
        " ".join(str(text or "").strip().split()).removeprefix("/").casefold()
        for text in texts
        if str(text or "").strip()
    }
    if not inputs:
        return None
    for item in _active_commands(context, event):
        if not getattr(item["plugin"], "reserved", False):
            continue
        native_ids = set(item["native_ids"])
        match = next(
            (name for name in _NATIVE_ORDER if name in native_ids and name in {"reset", "new"}),
            None,
        )
        if match and any(name.casefold() in inputs for name in item["names"]):
            return match
    return None


def _platform_aliases(event: Any) -> set[str]:
    getter = getattr(event, "get_platform_name", None)
    platform = str(getter() if callable(getter) else "").strip().casefold()
    aliases = {platform} if platform else set()
    if platform in {"weixin_oc", "weixin_official_account"}:
        aliases.update({"weixin_oc", "weixin_official_account"})
    return aliases


def _plugin_enabled_for_event(plugin: Any, event: Any, plugins_name: Sequence[str] | None) -> bool:
    if not plugin or not getattr(plugin, "activated", False):
        return False
    if getattr(plugin, "star_cls", None) is None:
        return False
    name = str(getattr(plugin, "name", "") or "")
    if (
        plugins_name is not None
        and not getattr(plugin, "reserved", False)
        and name not in plugins_name
        and "*" not in plugins_name
    ):
        return False
    supported = getattr(plugin, "support_platforms", None) or ()
    return not supported or bool(
        _platform_aliases(event)
        & {str(item).casefold() for item in supported}
    )


def _checkin_disabled(config: Any) -> bool:
    if not isinstance(config, Mapping):
        return False
    for key, value in config.items():
        normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if normalized in {"checkin", "enablecheckin", "checkinenabled", "featurecheckin"}:
            if value is False:
                return True
            if isinstance(value, Mapping) and any(
                nested_key in {"enabled", "enable", "active"} and nested_value is False
                for nested_key, nested_value in (
                    (str(k).casefold(), v) for k, v in value.items()
                )
            ):
                return True
        if isinstance(value, Mapping) and _checkin_disabled(value):
            return True
    return False


def _is_checkin_command(names: Sequence[str], description: str, plugin_name: str) -> bool:
    searchable = " ".join((*names, description, plugin_name)).casefold()
    return any(token in searchable for token in ("checkin", "check-in", "check_in", "签到", "打卡"))


def _permission_allowed(filters: Sequence[Any], event: Any, config: Any) -> bool:
    from astrbot.core.star.filter.permission import PermissionTypeFilter

    for current_filter in filters:
        if isinstance(current_filter, PermissionTypeFilter):
            try:
                if not current_filter.filter(event, config):
                    return False
            except Exception:
                _LOGGER.debug("权限筛选器失败，按无权限处理", exc_info=True)
                return False
    return True


def _active_commands(context: Any, event: Any) -> list[dict[str, Any]]:
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.filter.command_group import CommandGroupFilter
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_handler import EventType, star_handlers_registry

    allowed_plugins = getattr(event, "plugins_name", None)
    if not isinstance(allowed_plugins, (list, tuple)):
        allowed_plugins = None
    config_getter = getattr(context, "get_config", None)
    config = config_getter(getattr(event, "unified_msg_origin", None)) if callable(config_getter) else {}
    handlers = star_handlers_registry.get_handlers_by_event_type(
        EventType.AdapterMessageEvent,
        plugins_name=list(allowed_plugins) if allowed_plugins is not None else None,
    )
    candidates: list[dict[str, Any]] = []
    owners_by_root: dict[str, set[str]] = defaultdict(set)

    for handler in handlers:
        if not getattr(handler, "enabled", False):
            continue
        plugin = star_map.get(getattr(handler, "handler_module_path", ""))
        if not _plugin_enabled_for_event(plugin, event, allowed_plugins):
            continue
        filters = getattr(handler, "event_filters", ()) or ()
        permission_filters = list(filters)
        for current_filter in filters:
            permission_filters.extend(
                getattr(current_filter, "custom_filter_list", ()) or ()
            )
        if not _permission_allowed(permission_filters, event, config):
            continue
        command_filters = [
            current_filter for current_filter in filters
            if isinstance(current_filter, (CommandFilter, CommandGroupFilter))
        ]
        if not command_filters:
            continue
        plugin_name = str(getattr(plugin, "name", "") or "")
        plugin_config = getattr(plugin, "config", None)
        description = str(getattr(handler, "desc", "") or "").strip()
        for current_filter in command_filters:
            try:
                names = [
                    str(name).strip()
                    for name in current_filter.get_complete_command_names()
                    if str(name).strip()
                ]
            except Exception:
                _LOGGER.debug("略过无法枚举命令的插件处理器", exc_info=True)
                continue
            if not names:
                continue
            if (
                _checkin_disabled(plugin_config)
                and _is_checkin_command(names, description, plugin_name)
            ):
                continue
            original_name = str(
                getattr(current_filter, "_original_command_name", "")
                or getattr(current_filter, "_original_group_name", "")
                or ""
            ).strip()
            original_root = original_name.split(maxsplit=1)[0].casefold() if original_name else ""
            native_ids = ({original_root} if original_root in _NATIVE_COMMANDS else set())
            actual_root = names[0].split(maxsplit=1)[0].casefold()
            if actual_root in _NATIVE_COMMANDS:
                native_ids.add(actual_root)
            module_path = str(getattr(plugin, "module_path", "") or getattr(handler, "handler_module_path", ""))
            for name in names:
                root = name.split(maxsplit=1)[0].casefold()
                owners_by_root[root].add(module_path)
            candidates.append({
                "names": names,
                "primary": names[0],
                "plugin": plugin,
                "plugin_name": plugin_name,
                "display_name": str(getattr(plugin, "display_name", "") or plugin_name),
                "description": description,
                "module_path": module_path,
                "native_ids": native_ids,
                "original_command_name": original_name,
            })

    output = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        primary = item["primary"]
        conflicted = any(
            len(owners_by_root[name.split(maxsplit=1)[0].casefold()]) > 1
            for name in item["names"]
        )
        if conflicted:
            continue
        item["conflicted"] = False
        key = (primary.casefold(), item["module_path"])
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def enabled_native_commands(context: Any, event: Any) -> tuple[str, ...]:
    """返回此平台、此会话及当前权限下已注册的原生命令。"""
    available = set()
    for item in _active_commands(context, event):
        if not getattr(item["plugin"], "reserved", False):
            continue
        if item["conflicted"]:
            continue
        available.update(item["native_ids"])
    return tuple(name for name in _NATIVE_ORDER if name in available)


def _command_lines(items: Sequence[dict[str, Any]], *, image: bool) -> tuple[str, ...]:
    lines = []
    for item in items:
        primary = item["primary"]
        native_id = next(
            (name for name in _NATIVE_ORDER if name in item["native_ids"]),
            None,
        )
        description = " ".join(
            str(_NATIVE_COMMANDS.get(native_id, item["description"])).split()
        )
        if image and len(description) > 20:
            description = description[:19].rstrip() + "…"
        label = f"/{primary}"
        if description:
            label += f"：{description}"
        lines.append(label)
    if image:
        if len(lines) <= 3:
            return tuple(lines)
        return (*lines[:2], f"其余 {len(lines) - 2} 项见文字版菜单")
    if len(lines) > 24:
        return (*lines[:24], f"另有 {len(lines) - 24} 项可在 AstrBot 插件页查看。")
    return tuple(lines)


def build_unified_menu_groups(
    context: Any,
    event: Any,
) -> tuple[
    tuple[tuple[str, tuple[str, ...], tuple[int, int, int]], ...],
    tuple[tuple[str, tuple[str, ...]], ...],
]:
    """保留原有五个功能组，附加适用的 AstrBot 命令和插件。"""
    from app.services.help_menu import _IMAGE_GROUPS, _TEXT_GROUPS

    commands = _active_commands(context, event)
    native_by_id: dict[str, dict[str, Any]] = {}
    for item in commands:
        if not getattr(item["plugin"], "reserved", False):
            continue
        for native_id in item["native_ids"]:
            native_by_id.setdefault(native_id, item)
    native_roots = set(native_by_id)
    image_native_ids = [
        native_id for native_id in ("new", "sid", "provider")
        if native_id in native_by_id
    ]
    native_image_lines = []
    short_descriptions = {"new": "新对话", "sid": "会话信息", "provider": "切换模型"}
    for index in range(0, len(image_native_ids), 2):
        group = image_native_ids[index:index + 2]
        native_image_lines.append("；".join(
            f"/{native_by_id[native_id]['primary']}：{short_descriptions[native_id]}"
            for native_id in group
        ))
    if native_roots - set(image_native_ids):
        native_image_lines.append("其他原生命令见文字版菜单。")
    if not native_image_lines:
        native_image_lines = ["当前平台暂无可用的原生命令。"]

    plugin_items = [
        item for item in commands
        if not getattr(item["plugin"], "reserved", False)
        and not item["native_ids"]
    ]
    plugin_items.sort(key=lambda item: (item["display_name"].casefold(), item["primary"].casefold()))
    plugin_image_lines = list(_command_lines(plugin_items, image=True))
    if not plugin_image_lines:
        plugin_image_lines = ["当前平台暂无启用的插件命令。"]
    plugin_text_lines = list(_command_lines(plugin_items, image=False))
    if not plugin_text_lines:
        plugin_text_lines = ["当前平台暂无启用的插件命令。"]

    is_admin = bool(getattr(event, "is_admin", lambda: False)())
    old_admin_image = _IMAGE_GROUPS[-1]
    old_admin_text = _TEXT_GROUPS[-1]
    if is_admin:
        management_image = old_admin_image
        management_text = old_admin_text
    else:
        management_image = ("管理", ("群管理和管理员指令仅管理员可用。",), old_admin_image[2])
        management_text = ("管理", ("群管理和 AstrBot 管理命令仅管理员可用。",))

    image_groups = (
        *tuple(_IMAGE_GROUPS[:5]),
        ("AstrBot 聊天", tuple(native_image_lines[:3]), (91, 145, 173)),
        ("启用插件", tuple(plugin_image_lines[:3]), (145, 117, 171)),
        management_image,
    )
    native_text_lines = tuple(
        f"/{native_by_id[name]['primary']}：{_NATIVE_COMMANDS[name]}"
        for name in _NATIVE_ORDER if name in native_by_id
    ) or ("当前平台暂无可用的原生命令。",)
    text_groups = (
        *tuple(_TEXT_GROUPS[:5]),
        ("AstrBot 聊天", native_text_lines),
        ("启用插件", tuple(plugin_text_lines)),
        management_text,
    )
    return image_groups, text_groups


def is_text_menu_request(text: str) -> bool:
    return menu_request_kind(text) == "text"


def _raw_event_for_reset(plugin: Any, event: Any) -> dict[str, Any] | None:
    get_name = getattr(event, "get_platform_name", None)
    name = str(get_name() if callable(get_name) else "").casefold()
    if any(tag in name for tag in ("weixin", "wechat", "wecom")):
        from app.astrbot_wechat import WeChatTransport

        platform_id = str(event.get_platform_id())
        account_id = str(getattr(getattr(event, "platform", None), "account_id", "") or "")
        key = (platform_id, account_id)
        bridges = getattr(plugin, "_wechat_bridges", {})
        bridge = bridges.get(key)
        if bridge is None:
            bridge = WeChatTransport(
                plugin.context,
                platform_id=platform_id,
                account_id=account_id or None,
                platform_name=name,
            )
            bridges[key] = bridge
        return bridge.bind_event(event)

    from collections.abc import Mapping

    raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
    return dict(raw_message) if isinstance(raw_message, Mapping) else None
