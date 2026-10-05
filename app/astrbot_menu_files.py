"""提前保存菜单，QQ 直接发送本机文件。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_QQ_SEND_ACTION_TIMEOUT_MS = 30_000
_QQ_SEND_OUTER_TIMEOUT_SECONDS = 35


class _MenuAudience:
    """只用来列命令，不产生消息或调用模型。"""

    unified_msg_origin = None

    def __init__(self, platform: str, *, admin: bool, group: bool, plugins: Any) -> None:
        self.platform = platform
        self.admin = admin
        self.group = group
        self.plugins_name = list(plugins) if isinstance(plugins, (list, tuple)) else None

    def get_platform_name(self) -> str:
        return self.platform

    def get_group_id(self) -> str:
        return "menu-preview" if self.group else ""

    def is_admin(self) -> bool:
        return self.admin

    def get_extra(self, key: str, default: Any = None) -> Any:
        return default


def _publish_menu_file(source: Path, name: str) -> None:
    """留一个好找的固定文件名，内容相同时不改文件。"""
    destination = source.parent / name
    content = source.read_bytes()
    if destination.is_file() and destination.read_bytes() == content:
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=source.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def prepare_default_menu_files(context: Any, settings: Any) -> int:
    """插件都加载好后，提前准备 QQ 和微信的本地菜单。"""
    from app.astrbot_menu import build_unified_menu_groups
    from app.services.help_menu import prepare_help_menu_file

    config = context.get_config()
    plugins = config.get("plugin_set")
    count = 0
    for platform, label in (("aiocqhttp", "qq"), ("weixin_oc", "wechat")):
        for group in (True, False):
            for admin in (False, True):
                audience = _MenuAudience(platform, admin=admin, group=group, plugins=plugins)
                image_groups, text_groups = build_unified_menu_groups(context, audience)
                source = await prepare_help_menu_file(
                    settings, image_groups=image_groups, text_groups=text_groups,
                )
                scope = "" if group else "-private"
                permission = "-admin" if admin else ""
                await asyncio.to_thread(
                    _publish_menu_file, source, f"menu-{label}{scope}{permission}.jpg",
                )
                count += 1
    return count


async def choose_event_menu_file(
    context: Any, event: Any, source: Path, image_groups: Any, text_groups: Any,
) -> Path:
    """默认配置直接用固定文件，单独配过的会话保留自己的菜单。"""
    from app.astrbot_menu import build_unified_menu_groups

    platform = str(event.get_platform_name()).casefold()
    if any(tag in platform for tag in ("qq", "aiocqhttp", "onebot")):
        preview_platform, label = "aiocqhttp", "qq"
    elif platform in {"weixin_oc", "weixin_official_account"}:
        preview_platform, label = "weixin_oc", "wechat"
    else:
        return source
    admin = bool(event.is_admin())
    group = bool(event.get_group_id())
    audience = _MenuAudience(
        preview_platform, admin=admin, group=group,
        plugins=context.get_config().get("plugin_set"),
    )
    if build_unified_menu_groups(context, audience) != (image_groups, text_groups):
        return source
    scope = "" if group else "-private"
    permission = "-admin" if admin else ""
    name = f"menu-{label}{scope}{permission}.jpg"
    await asyncio.to_thread(_publish_menu_file, source, name)
    return source.with_name(name).resolve(strict=True)


async def send_menu_file(event: Any, path: Path) -> None:
    """QQ 绕过图片转 base64，其他平台沿用本地图片组件。"""
    platform = str(event.get_platform_name()).casefold()
    if not any(tag in platform for tag in ("qq", "aiocqhttp", "onebot")):
        await event.send(event.make_result().file_image(str(path)))
        return

    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if not isinstance(raw, Mapping):
        raise TypeError("QQ 菜单缺少消息来源")
    message_type = raw.get("message_type")
    if message_type == "group":
        action, target = "send_group_msg", "group_id"
    elif message_type == "private":
        action, target = "send_private_msg", "user_id"
    else:
        raise ValueError("QQ 菜单消息类型不支持")
    target_id = str(raw.get(target) or "")
    if not target_id.isascii() or not target_id.isdigit() or int(target_id) <= 0:
        raise ValueError("QQ 菜单缺少有效接收方")
    params: dict[str, Any] = {
        target: int(target_id),
        "message": [{"type": "image", "data": {"file": path.resolve().as_uri()}}],
        # NapCat 按消息大小计算上传确认时间，菜单发送单独放宽时限。
        "timeout": _QQ_SEND_ACTION_TIMEOUT_MS,
    }
    self_id = raw.get("self_id")
    if self_id is not None:
        params["self_id"] = str(self_id)
    await asyncio.wait_for(
        event.bot.api.call_action(action, **params),
        timeout=_QQ_SEND_OUTER_TIMEOUT_SECONDS,
    )
    event._has_send_oper = True


def menu_send_error_info(error: Exception) -> tuple[str, str, str]:
    """只记错误类别和返回码，避免把消息、账号或图片打进日志。"""
    result = getattr(error, "result", None)
    result = result if isinstance(result, Mapping) else {}
    retcode = result.get("retcode")
    code = str(retcode) if isinstance(retcode, int) else "未知"
    messages = " ".join(
        value[:2000] for key in ("message", "wording")
        if isinstance(value := result.get(key), str)
    ).casefold()
    if isinstance(error, TimeoutError) or "timeout" in messages or "超时" in messages:
        reason = "确认超时"
    elif "ntevent" in messages or "sendmsg" in messages:
        reason = "QQ消息发送失败"
    elif "file" in messages or "文件" in messages:
        reason = "文件处理失败"
    else:
        reason = "协议或平台发送失败"
    return type(error).__name__, code, reason
