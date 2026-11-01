"""从已加载插件读取本群设置，不触发调度、修改配置或调用模型。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_PROACTIVE = "astrbot_plugin_proactive_chat"
_COMPANION = "astrbot_plugin_private_companion"


def _loaded_plugin(context: Any, name: str, event: Any = None) -> tuple[Any, Mapping] | None:
    allowed = getattr(event, "plugins_name", None)
    if isinstance(allowed, (list, tuple)) and name not in allowed and "*" not in allowed:
        return None
    getter = getattr(context, "get_all_stars", None)
    if not callable(getter):
        return None
    for plugin in getter():
        if getattr(plugin, "name", None) != name or not getattr(plugin, "activated", False):
            continue
        instance = getattr(plugin, "star_cls", None)
        if instance is None:
            return None
        config = getattr(instance, "config", None)
        if not isinstance(config, Mapping):
            config = getattr(plugin, "config", None)
        return instance, config if isinstance(config, Mapping) else {}
    return None


def _group_origin(event: Any) -> tuple[str, str] | None:
    getter = getattr(event, "get_group_id", None)
    group = str(getter() or "").strip() if callable(getter) else ""
    origin = str(getattr(event, "unified_msg_origin", "") or "")
    parts = origin.split(":", 2)
    if not group or len(parts) != 3 or parts[1] != "GroupMessage" or parts[2] != group:
        return None
    return group, origin


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _minutes(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return f"{value:g}"
    return "未设置"


def proactive_group_status(context: Any, event: Any) -> str:
    """只显示当前群是否命中及其最终生效频率，不列其他会话。"""
    identity = _group_origin(event)
    if identity is None:
        return "请在要查看的群里发送“主动消息”，这里按当前群读取设置。"
    loaded = _loaded_plugin(context, _PROACTIVE, event)
    if loaded is None:
        return "本群的主动消息插件当前未启用。修改位置：AstrBot面板 → 插件 → ProactiveChat。"
    instance, config = loaded
    group_id, origin = identity
    base = _mapping(config.get("group_settings"))
    sessions = base.get("session_list", [])
    matched = isinstance(sessions, (list, tuple, set)) and any(
        candidate in sessions for candidate in (origin, group_id)
    )
    effective: Mapping = base
    getter = getattr(instance, "_get_session_config", None)
    if callable(getter):
        try:
            resolved = getter(origin)
        except Exception:  # noqa: BLE001 -- 第三方只读getter失败时不把异常或配置内容发到群里。
            return "本群主动消息设置暂时读取失败，请到面板 → 插件 → ProactiveChat 查看。"
        if isinstance(resolved, Mapping):
            effective = resolved
            matched = bool(resolved.get("_from_session_list", matched))
        else:
            matched = False
    enabled = bool(base.get("enable", False) and matched and effective.get("enable", False))
    schedule = _mapping(effective.get("schedule_settings"))
    minimum = _minutes(schedule.get("min_interval_minutes"))
    maximum = _minutes(schedule.get("max_interval_minutes"))
    interval = f"{minimum}～{maximum}分钟" if "未设置" not in (minimum, maximum) else "未设置"
    idle = _minutes(effective.get("group_idle_trigger_minutes"))
    idle_text = f"{idle}分钟" if idle != "未设置" else idle
    quiet = str(schedule.get("quiet_hours", "") or "").strip()[:60] or "无"
    unanswered = schedule.get("max_unanswered_times")
    unanswered_text = f"{unanswered}次" if isinstance(unanswered, int) and not isinstance(unanswered, bool) else "未设置"
    state = "开启" if enabled else "关闭"
    if not matched:
        state += "（本群未命中目标会话）"
    return (
        f"本群主动消息：{state}\n"
        f"沉默触发：{idle_text}；随机间隔：{interval}\n"
        f"免打扰：{quiet}；未回复上限：{unanswered_text}\n"
        "修改位置：AstrBot面板 → 插件 → ProactiveChat → 群聊/会话设置。"
    )


def companion_photo_requires_private(context: Any, event: Any = None) -> bool:
    """供群菜单和入口共用范围判断，不修改Companion权限或生图机制。"""
    loaded = _loaded_plugin(context, _COMPANION, event)
    if loaded is None:
        return False
    instance, config = loaded
    enabled = getattr(instance, "require_private_opt_in", None)
    if isinstance(enabled, bool):
        return enabled
    for settings in (_mapping(config.get("proactive_reach_config")), config):
        value = settings.get("require_private_opt_in")
        if isinstance(value, bool):
            return value
    return False


def private_companion_photo_hint(context: Any, event: Any) -> str | None:
    """仅提示明确的私聊专用命令，普通群聊生图意图继续走原机制。"""
    if _group_origin(event) is None:
        return None
    text = str(event.get_message_str() or "").strip()
    if not re.fullmatch(r"/?陪伴\s+(?:自拍|生图|画图)(?:\s+.+)?", text, flags=re.DOTALL):
        return None
    if not companion_photo_requires_private(context, event):
        return None
    return "这个入口在私聊使用：陪伴 自拍／陪伴 生图 描述；群漫画可用“群漫画”。"
