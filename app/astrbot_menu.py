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
    "文字版功能", "纯文字菜单", "画境拾珍菜单", "画境拾珍帮助",
    "功能说明", "详细菜单", "使用说明", "详细帮助",
})
_LOGGER = logging.getLogger(__name__)
_PLUGIN_DISPLAY_NAMES = {
    "astrbot_plugin_lifesim": "模拟人生",
    "astrbot_plugin_private_companion": "日常陪伴",
    "astrbot_plugin_qq_group_daily_analysis": "群聊分析",
    "astrbot_plugin_self_learning": "学习设置",
    "astrbot_plugin_qqchat": "主动消息",
    "meme_manager": "表情包",
}
_COMMON_PLUGIN_COMMANDS = {
    # 生图服务的工作流维护由面板管理，用户入口统一通过“陪伴 生图”。
    "astrbot_plugin_image_companion": (),
    "astrbot_plugin_qqchat": (("主动消息", "查看本群主动消息设置"),),
    "astrbot_plugin_lifesim": (
        ("人生开启", "开始人生"), ("人生继续", "继续剧情"), ("人生选择", "选择行动"),
        ("人生加点", "分配属性"), ("人生状态", "查看状态"), ("人生重置", "重新开始"),
    ),
    "astrbot_plugin_private_companion": (("陪伴", "个人状态与设置"), ("陪伴群", "群聊状态与开关")),
    "astrbot_plugin_self_learning": (
        ("learning_status", "学习状态"), ("start_learning", "开启学习"),
        ("stop_learning", "停止学习"), ("set_mood", "设置心情"), ("affection_status", "查看好感"),
    ),
    "astrbot_plugin_qq_group_daily_analysis": (
        ("群分析", "生成群报告"), ("群漫画", "生成群漫画"), ("设置格式", "设置报告格式"),
        ("设置模板", "设置报告模板"), ("查看模板", "查看可用模板"), ("分析设置", "查看分析设置"),
    ),
    "meme_manager": (
        ("表情管理", "图库与表情设置"), ("表情管理 查看图库", "查看图库"),
        ("表情管理 添加表情", "添加表情"), ("表情管理 图库统计", "查看图库统计"),
    ),
}


def menu_request_kind(text: str) -> str | None:
    """只识别短而明确的菜单请求。"""
    if not isinstance(text, str):
        return None
    from app.services.music_selection import parse_music_action
    from app.services.story_generation import parse_story_request

    # 歌名和故事素材也可能含菜单词，先留给各自入口。
    if parse_story_request(text) is not None or parse_music_action(text) is not None:
        return None
    from app.services.qq_feature_menu import requested_topic

    if requested_topic(text):
        return "text"
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
                "handler_name": str(getattr(handler, "handler_name", "") or ""),
                "is_group": isinstance(current_filter, CommandGroupFilter),
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


def _command_path(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _plugin_menu_items(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """去掉没有任何当前用户可用叶命令的指令组父项。"""
    leaves = [item for item in items if not item.get("is_group", False)]
    output = []
    for item in items:
        if not item.get("is_group", False):
            output.append(item)
            continue
        roots = tuple(_command_path(name) for name in item["names"])
        has_visible_child = any(
            child["module_path"] == item["module_path"]
            and any(
                _command_path(name).startswith(root + " ")
                for name in child["names"]
                for root in roots
            )
            for child in leaves
        )
        if has_visible_child:
            output.append(item)
    return output


def _find_plugin_command(
    items: Sequence[dict[str, Any]],
    *,
    handler_names: Sequence[str],
    command_names: Sequence[str],
) -> dict[str, Any] | None:
    wanted_handlers = {name.casefold() for name in handler_names}
    wanted_names = {_command_path(name) for name in command_names}
    for item in items:
        if item.get("handler_name", "").casefold() in wanted_handlers:
            return item
    return next(
        (
            item for item in items
            if wanted_names.intersection(
                {_command_path(item.get("original_command_name", "")),
                 *(_command_path(name) for name in item["names"])}
            )
        ),
        None,
    )


def _anime_plugin_image_lines(items: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    """按当前用户可用命令生成画境拾珍的图片菜单摘要。"""
    search = _find_plugin_command(
        items,
        handler_names=("cmd_p",),
        command_names=("p",),
    )
    checkin = _find_plugin_command(
        items,
        handler_names=("cmd_checkin",),
        command_names=("签到",),
    )
    status = _find_plugin_command(
        items,
        handler_names=("cmd_checkin_status",),
        command_names=("签到我的 状态",),
    )
    month_ranking = _find_plugin_command(
        items,
        handler_names=("cmd_checkin_ranking_month",),
        command_names=("签到排行 月榜",),
    )
    shop = _find_plugin_command(
        items,
        handler_names=("cmd_checkin_shop",),
        command_names=("签到商店 查看",),
    )

    lines = []
    if search:
        lines.append(f"搜图：/{search['primary']} [标签] [数量]")
    checkin_parts = []
    if checkin:
        checkin_parts.append(f"签到：/{checkin['primary']}")
    if status:
        checkin_parts.append(f"状态：/{status['primary']}")
    if checkin_parts:
        lines.append("；".join(checkin_parts))
    activity_parts = []
    if month_ranking:
        activity_parts.append(f"月榜：/{month_ranking['primary']}")
    if shop:
        activity_parts.append(f"商店：/{shop['primary']}")
    if activity_parts:
        lines.append("；".join(activity_parts))
    return tuple(lines)


def _auto_trigger_enabled(plugin: Any) -> bool:
    config = getattr(plugin, "config", None)
    if isinstance(config, Mapping):
        return config.get("auto_trigger_enabled") is True
    return getattr(config, "auto_trigger_enabled", False) is True


def _complete_plugin_text_lines(items: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    """只列选定的可调用入口，不展开插件全部能力和内部说明。"""
    lines = []
    for item in items:
        description = str(item.get("menu_label") or item["description"]).split("\n", 1)[0].strip()
        description = re.split(r"[。！？]", description, maxsplit=1)[0][:36]
        label = f"/{item['primary']}"
        if description:
            label += f"：{description}"
        if (
            item["plugin_name"] == "astrbot_plugin_get_px"
            and item.get("handler_name") == "cmd_p"
        ):
            label += "；不填标签即随机发图"
        lines.append(label)
    return tuple(lines)


def _common_plugin_menu_items(items: Sequence[dict[str, Any]], event: Any) -> list[dict[str, Any]]:
    if not items:
        return []
    selected = _COMMON_PLUGIN_COMMANDS.get(items[0]["plugin_name"])
    if selected is None:
        return list(items[:6])
    output = []
    for name, label in selected:
        if items[0]["plugin_name"] == "astrbot_plugin_private_companion":
            in_group = bool(getattr(event, "get_group_id", lambda: "")())
            if (name == "陪伴" and in_group) or (name == "陪伴群" and not in_group):
                continue
        item = _find_plugin_command(items, handler_names=(), command_names=(name,))
        if item is None:
            continue
        primary = next((alias for alias in item["names"] if _command_path(alias) == _command_path(name)), item["primary"])
        output.append({**item, "primary": primary, "menu_label": label})
    return output


def _build_detailed_menu_groups(
    context: Any,
    event: Any,
    settings: Any = None,
) -> tuple[
    tuple[tuple[str, tuple[str, ...], tuple[int, int, int]], ...],
    tuple[tuple[str, tuple[str, ...]], ...],
]:
    """保留基础功能，仅附加当前用户可调用或设置的常用插件入口。"""
    from app.services.help_menu import _IMAGE_GROUPS, _TEXT_GROUPS, configured_menu_groups
    base_images, base_texts = configured_menu_groups(settings) if settings is not None else (_IMAGE_GROUPS, _TEXT_GROUPS)

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

    plugin_items = [
        item for item in commands
        if not getattr(item["plugin"], "reserved", False)
        and not item["native_ids"]
    ]
    plugin_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in plugin_items:
        group_key = (
            item["module_path"],
            item["display_name"] or item["plugin_name"],
        )
        plugin_groups[group_key].append(item)

    image_plugin_groups: list[tuple[str, tuple[str, ...], tuple[int, int, int]]] = []
    text_plugin_groups: list[tuple[str, tuple[str, ...]]] = []
    for (_, display_name), unsorted_items in sorted(
        plugin_groups.items(),
        key=lambda pair: (pair[0][1].casefold(), pair[0][0].casefold()),
    ):
        group_items = _plugin_menu_items(unsorted_items)
        if not group_items:
            continue
        group_items.sort(key=lambda item: item["primary"].casefold())
        plugin_name = group_items[0]["plugin_name"]
        is_anime_plugin = any(
            item["plugin_name"] == "astrbot_plugin_get_px" for item in group_items
        )
        if is_anime_plugin:
            image_lines = _anime_plugin_image_lines(group_items)
            text_lines = list(image_lines)
            if any(item.get("handler_name") == "cmd_p" for item in group_items):
                text_lines.append("题材插画：来份色图 / 来张泳装图 / 来张丝袜女仆图；每次一张，非R18")
            if any(_auto_trigger_enabled(item["plugin"]) for item in group_items):
                text_lines.append("自然语言发图：来一份图；来张风景图")
        else:
            group_items = _common_plugin_menu_items(group_items, event)
            if not group_items:
                continue
            image_lines = tuple(
                "；".join(f"/{item['primary']}" for item in group_items[index:index + 2])
                for index in range(0, min(len(group_items), 6), 2)
            )
            text_lines = list(_complete_plugin_text_lines(group_items))
        display_name = _PLUGIN_DISPLAY_NAMES.get(plugin_name, display_name)
        if image_lines:
            image_plugin_groups.append(
                (display_name, tuple(image_lines[:3]), (145, 117, 171))
            )
        if text_lines:
            text_plugin_groups.append((display_name, tuple(text_lines)))

    is_admin = bool(getattr(event, "is_admin", lambda: False)())
    old_admin_image = base_images[-1]
    old_admin_text = base_texts[-1]
    if is_admin:
        management_image = old_admin_image
        management_text = old_admin_text

    image_groups = (
        *tuple(base_images[:-1]),
        *((("AstrBot 聊天", tuple(native_image_lines[:3]), (91, 145, 173)),) if native_image_lines else ()),
        *tuple(image_plugin_groups),
        *((management_image,) if is_admin else ()),
    )
    native_text_lines = tuple(
        f"/{native_by_id[name]['primary']}：{_NATIVE_COMMANDS[name]}"
        for name in _NATIVE_ORDER if name in native_by_id
    )
    text_groups = (
        *tuple(base_texts[:-1]),
        *((("AstrBot 聊天", native_text_lines),) if native_text_lines else ()),
        *tuple(text_plugin_groups),
        *((management_text,) if is_admin else ()),
    )
    return image_groups, text_groups


def _enabled_menu_plugins(context: Any, event: Any) -> set[str]:
    """补上仅提供面板设置、没有聊天命令的已启用插件。"""
    getter = getattr(context, "get_all_stars", None)
    if not callable(getter):
        return set()
    allowed = getattr(event, "plugins_name", None)
    if not isinstance(allowed, (list, tuple)):
        allowed = None
    return {
        str(getattr(plugin, "name", ""))
        for plugin in getter()
        if _plugin_enabled_for_event(plugin, event, allowed)
    }


def build_unified_menu_groups(context: Any, event: Any, settings: Any = None):
    """精简基础功能，并让已启用插件在图片和文字中都有常用入口。"""
    from app.services.help_menu import _IMAGE_GROUPS, _TEXT_GROUPS, configured_menu_groups

    _, detailed = _build_detailed_menu_groups(context, event, settings)
    base_images, base = configured_menu_groups(settings) if settings is not None else (_IMAGE_GROUPS, _TEXT_GROUPS)
    base_titles = {title for title, _ in base} | {"AstrBot 聊天", "管理"}
    plugins = [(title, lines) for title, lines in detailed if title not in base_titles]
    plugin_lines = {}
    plugin_image_lines = {}
    plugin_commands = {}
    for title, lines in plugins:
        selected = list(lines)
        if title == "学习设置":
            selected = [line for line in selected if line.endswith(("开启学习", "停止学习"))] or selected
        elif title == "群聊分析":
            selected = [line for line in selected if line.endswith(("生成群报告", "查看分析设置"))] or selected
        labels = [line.split("：", 1)[0] for line in selected if line.startswith("/")][:2]
        if labels:
            plugin_commands[title] = labels
            plugin_lines[title] = f"{title}：" + "；".join(labels)
            overview = next((line for line in lines if line.endswith("学习状态")), selected[0])
            plugin_image_lines[title] = f"{title}：{overview.split('：', 1)[0]}"
            if title == "学习设置" and overview.endswith("学习状态"):
                plugin_lines[title] = f"{title}：{overview.split('：', 1)[0]}；开关：" + " / ".join(labels)

    enabled_plugins = _enabled_menu_plugins(context, event)
    in_group = bool(getattr(event, "get_group_id", lambda: "")())
    is_admin = bool(getattr(event, "is_admin", lambda: False)())
    # 普通用户仍能知道插件有哪些功能，但不展示无权限执行的管理命令。
    for name, title, description in (
        ("astrbot_plugin_self_learning", "学习设置", "表达 / 黑话（管理员设置）"),
        ("astrbot_plugin_qq_group_daily_analysis", "群聊分析", "群日报（管理员设置）"),
    ):
        if name not in enabled_plugins or title in plugin_lines:
            continue
        if name == "astrbot_plugin_qq_group_daily_analysis" and not in_group:
            continue
        plugin_lines[title] = plugin_image_lines[title] = f"{title}：{description}"
    if "astrbot_plugin_proactive_chat" in enabled_plugins:
        description = "频率 / 免打扰（面板设置）" if is_admin else "闲时聊天（管理员设置）"
        if "主动消息" not in plugin_lines:
            plugin_lines["主动消息"] = plugin_image_lines["主动消息"] = f"主动消息：{description}"
    else:
        plugin_lines.pop("主动消息", None)
        plugin_image_lines.pop("主动消息", None)

    image_actions = []
    if {"astrbot_plugin_private_companion", "astrbot_plugin_image_companion"} <= enabled_plugins:
        photo_root = _find_plugin_command(
            [item for item in _active_commands(context, event)
             if item["plugin_name"] == "astrbot_plugin_private_companion"],
            handler_names=("companion_command",), command_names=("陪伴",),
        )
        if photo_root:
            root = "/" + photo_root["primary"]
            from app.astrbot_plugin_status import companion_photo_requires_private

            private_photo = in_group and companion_photo_requires_private(context, event)
            if private_photo:
                image_actions.extend((f"私聊生图：{root} 生图 描述", f"私聊自拍：{root} 自拍"))
                plugin_lines["陪伴生图"] = f"陪伴生图（私聊）：{root} 生图 描述；{root} 自拍"
            else:
                image_actions.extend((f"生图：{root} 生图 画面描述", f"自拍：{root} 自拍"))
                plugin_lines["陪伴生图"] = f"陪伴生图：{root} 生图 画面描述；{root} 自拍"
    report_lines = next((lines for title, lines in plugins if title == "群聊分析"), ())
    format_controls = [line.split("：", 1)[0] for line in report_lines
                       if line.endswith(("设置报告格式", "设置报告模板"))]
    if format_controls:
        plugin_lines["群报设置"] = "群报设置：" + "；".join(format_controls[:2])
    comic = next((line.split("：", 1)[0] for line in report_lines if line.endswith("生成群漫画")), "")
    if comic:
        image_actions.append("群漫画：" + comic)
        plugin_lines["群漫画"] = "群漫画：" + comic
    elif not is_admin and in_group and "astrbot_plugin_qq_group_daily_analysis" in enabled_plugins:
        image_actions.append("群漫画：管理员设置")

    # 原功能的独立卡片必须保留；只有角色全名单统一收进“可用角色”。
    merged_titles = {"聊天陪伴", "语音翻唱", "点歌视频", "实用工具", "互动小游戏", "管理"}
    catalog_titles = {"MyGO!!!!! 角色", "Ave Mujica 角色"}
    extra_images = tuple(group for group in base_images if group[0] not in merged_titles | catalog_titles)
    extra_text_lines = []
    for title, _, _ in extra_images:
        if title == "雀魂牌谱分析":
            extra_text_lines.append("雀魂牌谱：雀魂分析＋官方链接；连接账号后读取整场；也支持 mjai/天凤JSON；/牌谱分析 帮助")
        else:
            content = next((lines for name, lines in base if name == title), ())
            if content:
                extra_text_lines.append(f"{title}：{content[0]}")

    native = next((lines for title, lines in detailed if title == "AstrBot 聊天"), ())
    controls = [line.split("：", 1)[0] for line in native if any(
        word in line for word in ("新对话", "短上下文", "切换当前会话模型", "停止当前生成")
    )][:2]
    setting_lines = ["记住：内容 / 删除记忆 关键词"]
    if controls:
        setting_lines.append("会话：" + " / ".join(controls))
    setting_lines.append("/bot on|off" if is_admin else "开启恋爱模式 / 关闭恋爱模式")
    image_groups = (
        ("聊天", ("@我聊天 / 随机故事",), (92, 117, 151)),
        ("语音翻唱", ("启动语音 / 关闭语音", "音色列表 / 选择音色 名字", "翻唱片段 音色 歌名", "翻唱完整 音色 歌名"), (177, 108, 145)),
        ("点歌视频", ("点歌 歌名 / 查歌词 歌名", "播放视频 关键词", "看视频：发视频＋总结一下"), (94, 147, 147)),
        ("实用工具", ("天气 上海 / 翻译 内容", "搜索 关键词 / 今日热点"), (129, 133, 188)),
        ("互动游戏", ("角色图鉴 名字 / 随机夺舍", "今日奥特曼 / 随机二次元角色"), (199, 151, 94)),
        ("开关设置", tuple(setting_lines), (122, 137, 158)),
        *extra_images,
    )
    chat_titles = [title for title in plugin_image_lines if title not in {"学习设置", "群聊分析"}]
    chat_titles.sort(key=lambda title: ({"模拟人生": 0, "日常陪伴": 1, "主动消息": 2}.get(title, 3), title))
    learning_lines = [plugin_image_lines[title] for title in ("学习设置", "群聊分析") if title in plugin_image_lines]
    calendar_report_command = _find_plugin_command(
        _active_commands(context, event), handler_names=("calendar_group_reports",), command_names=("群报",),
    )
    if "astrbot_plugin_qq_group_daily_analysis" in enabled_plugins and calendar_report_command:
        report_line = "群报：昨天＋今天（截至上一分钟）"
        learning_lines = [line for line in learning_lines if not line.startswith("群聊分析：")]
        learning_lines.append(report_line)
        plugin_lines["群聊分析"] = "群报：/群报（昨天＋今天）"
    report_controls = plugin_commands.get("群聊分析", [])
    if len(report_controls) > 1:
        learning_lines.append("群报设置：" + report_controls[1])
    for title, entries in (
        ("插件 · 陪伴互动", [plugin_image_lines[title] for title in chat_titles]),
        ("插件 · 学习群报", learning_lines),
        ("生图与群漫画", image_actions),
    ):
        for offset in range(0, len(entries), 3):
            image_groups += ((title, tuple(entries[offset:offset + 3]), (145, 117, 171)),)
    lines = [
        "聊天：@我；随机故事",
        "语音翻唱：启动语音 / 关闭语音；翻唱片段 角色 歌名",
        "点歌视频：点歌 歌名；播放视频 关键词；发视频＋总结一下",
        "实用工具：天气 上海；翻译 内容；搜索 关键词；今日热点",
        "互动：角色图鉴 名字；随机二次元角色；随机夺舍",
        "记忆：记住：内容；删除记忆 关键词",
        *extra_text_lines,
        *plugin_lines.values(),
    ]
    if controls:
        lines.append("会话设置：" + "；".join(controls))
    if is_admin:
        lines.append("群管理：/bot on|off")
    from app.astrbot_qq_persona import is_qq

    if is_qq(event):
        from app.services.qq_feature_menu import core_groups

        core_images, core_texts = core_groups(settings=settings, controls=controls, is_admin=is_admin)
        additions = tuple(group for group in image_groups if group[0].startswith("插件 ·") or group[0] == "生图与群漫画")
        image_groups = (*core_images, *extra_images, *additions)
        detailed_groups = list(core_texts)
        image_groups = tuple(
            ("群报与设置" if title == "插件 · 学习群报" and "学习设置" not in plugin_lines else title, rows, color)
            for title, rows, color in image_groups
        )
        for title, _, _ in extra_images:
            if title == "雀魂牌谱分析":
                detailed_groups.append((title, (
                    "雀魂牌谱：雀魂分析 + 已结束的官方牌谱链接；连接雀魂账号后读取整场，提供本人固定座位和东风/半庄",
                    "也支持mjai、天凤兼容JSON；逐局分析切牌、场况和攻守，缺资料会先提示补充",
                    "/牌谱分析 帮助：看导入方法；/牌谱分析 详情 复盘ID 节点号 [页码]：追问；/牌谱分析 导出 复盘ID：保存报告",
                    "雀魂账号只在本机设置页连接，不把账号密码发进群",
                )))
            else:
                content = next((items for name, items in base if name == title), ())
                if content:
                    detailed_groups.append((title, tuple(content)))
        plugin_descriptions = {
            "日常陪伴": "查看本群或私聊的陪伴状态与可用开关，管理员设置按当前权限显示",
            "主动消息": "查看主动接话设置；当前群里被点名才回应，冷场时按已有安排偶尔开口",
            "群聊分析": "群报：昨天＋今天（截至上一分钟）；群分析：按已配置范围生成群聊图文总结",
            "群报设置": "设置报告的输出格式和模板；改设置需有当前群的管理权限",
            "群漫画": "根据本群近期实际话题生成漫画与文字说明，不把旧话题当最新内容",
            "陪伴生图": "给出画面描述来创作新图；标注私聊的入口请在私聊使用",
            "表情包": "查看图库、添加表情或看统计；日常表情由语境选择，不会每句都发",
            "B站 AI Bot": "查询B站分区与B站助手用法；视频搜索、观看总结见“视频与看图”",
        }
        plugin_details = dict(plugins)
        for title, summary in plugin_lines.items():
            entries = tuple(plugin_details.get(title, ()))
            # These entries were already filtered against live commands,
            # per-session plugin switches and the current user's permission.
            body = entries[:6] if entries and title not in {"群漫画", "陪伴生图", "群报设置"} else (summary,)
            note = plugin_descriptions.get(title)
            if note:
                body = (*body, note)
            if title == "群聊分析" and calendar_report_command:
                body = tuple(line for line in body if not line.startswith("群报："))
                if note in body:
                    body = tuple(line for line in body if line != note)
                body = ("群报：/" + calendar_report_command["primary"] + "（昨天＋今天，截至上一分钟）", *body)
            detailed_groups.append((title, body))
        session_lines = tuple(line for line in native if any(command in line for command in controls))
        if is_admin:
            session_lines += ("/bot on|off：开启或关闭本群机器人",)
        if session_lines:
            detailed_groups.append(("会话与管理", session_lines))
        detailed_groups.append(("菜单用法", (
            "菜单：简洁图片；文字版菜单 / 功能说明：分类用法；网易云帮助 / 视频帮助 / 唱歌帮助：只看这一项",
        )))
        return tuple(image_groups), tuple(detailed_groups)
    return image_groups, (("常用命令", tuple(lines)),)


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
