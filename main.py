"""AstrBot 插件入口，复用现有 QQ 机器人运行时。"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

_LOGGER = logging.getLogger(__name__)


def _find_project_root() -> Path:
    """从插件文件位置向上查找项目根目录。"""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "app" / "main.py").is_file() and (
            candidate / "pyproject.toml"
        ).is_file():
            return candidate
    raise RuntimeError(
        "未能从插件路径找到 QQ 机器人项目根目录，请从仓库根目录安装插件。"
    )


def _get_result_or_none(event: AstrMessageEvent) -> Any | None:
    """读取前置处理器留下的事件结果。"""
    try:
        return event.get_result()
    except AttributeError:
        return None


def _is_stopped(event: AstrMessageEvent) -> bool:
    """检查事件是否已停止传播。"""
    is_stopped = getattr(event, "is_stopped", None)
    return bool(is_stopped()) if callable(is_stopped) else False


class QQChatPlugin(Star):
    """把 OneBot v11 消息交给本地机器人运行时。"""

    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.legacy_chat = bool(self.config.get("legacy_chat", True))
        self.runtime: Any | None = None
        self._wechat_bridges: dict[tuple[str, str], Any] = {}

    async def initialize(self) -> None:
        """初始化现有服务，不启动旧 Web 服务。"""
        if self.runtime is not None:
            return

        # 插件通常由 AstrBot 从 data/plugins 加载，项目根目录未必在导入路径中。
        project_root = _find_project_root()
        root_string = str(project_root)
        if root_string not in sys.path:
            # 加入仓库路径以便导入 app 包，不改变工作目录。
            sys.path.insert(0, root_string)

        from app.astrbot_runtime import ChatRuntime

        runtime = ChatRuntime(
            project_root=project_root,
            legacy_chat=self.legacy_chat,
        )
        try:
            await runtime.initialize()
            bind_models = getattr(runtime, "bind_astrbot_models", None)
            if callable(bind_models):
                await bind_models(self.context)
        except BaseException:
            # 清理半初始化的服务，保留原来的异常。
            with suppress(BaseException):
                await runtime.close()
            raise
        self.runtime = runtime

    async def terminate(self) -> None:
        """卸载插件时释放队列和后台任务。"""
        runtime, self.runtime = self.runtime, None
        try:
            if runtime is not None:
                await runtime.close()
        finally:
            self._wechat_bridges.clear()

    async def _legacy_event_allowed(self, raw_event: Mapping[str, Any] | None) -> bool:
        """沿用旧群开关和黑名单，避免高优先级菜单绕过限制。"""
        if raw_event is None:
            return False
        if raw_event.get("message_type") != "group":
            return True
        group_id = str(raw_event.get("group_id") or "").strip()
        user_id = str(raw_event.get("user_id") or "").strip()
        core = getattr(self.runtime, "_core", None)
        database = getattr(getattr(core, "app", None), "state", None)
        database = getattr(database, "db", None)
        if not group_id or not user_id or database is None:
            return False
        try:
            if not await database.group_enabled(group_id):
                return False
            return not await database.is_blocked(group_id, user_id)
        except Exception:
            _LOGGER.exception("检查旧群开关或黑名单失败，暂不处理菜单请求")
            return False

    @filter.on_decorating_result()
    async def filter_qq_source_links(self, event: AstrMessageEvent) -> None:
        """插件和原生回复里的国外链接也改成文字来源。"""
        get_name = getattr(event, "get_platform_name", None)
        platform = str(get_name() if callable(get_name) else "").casefold()
        if not any(tag in platform for tag in ("qq", "aiocqhttp", "onebot")):
            return
        from astrbot.api.message_components import Plain

        from app.services.outbound_links import sanitize_qq_source_links

        result = event.get_result()
        for component in getattr(result, "chain", ()) or ():
            if isinstance(component, Plain):
                component.text = sanitize_qq_source_links(component.text)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=110)
    async def on_native_context_reset(self, event: AstrMessageEvent) -> None:
        """清除旧短上下文后让 AstrBot 继续处理 /reset 或 /new。"""
        if self.runtime is None:
            return

        original = event.get_extra("astrbot_original_message_str", "") or ""
        current = event.get_message_str()
        from app.astrbot_menu import enabled_native_commands, native_reset_command

        command = native_reset_command(self.context, event, original, current)
        if command is None:
            return

        from astrbot.core.star.filter.permission import (
            PermissionType,
            PermissionTypeFilter,
        )

        config = self.context.get_config(getattr(event, "unified_msg_origin", None))
        allowed = PermissionTypeFilter(
            PermissionType.SHARED_GROUP_ADMIN,
            raise_error=False,
        ).filter(event, config)
        if not allowed:
            return

        from app.astrbot_menu import _raw_event_for_reset

        raw_event = _raw_event_for_reset(self, event)
        if not await self._legacy_event_allowed(raw_event):
            return

        if command in enabled_native_commands(self.context, event):
            self.runtime.clear_chat_context(raw_event)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_unified_help_menu(self, event: AstrMessageEvent):
        """回复图片或文字菜单，并停止后续重复聊天。"""
        if (
            self.runtime is None
            or _is_stopped(event)
            or _result_has_reply(_get_result_or_none(event))
            or getattr(event, "_has_send_oper", False)
        ):
            return

        from app.astrbot_menu import build_unified_menu_groups, menu_request_kind

        request_kind = menu_request_kind(event.get_message_str())
        if request_kind is None:
            request_kind = menu_request_kind(
                event.get_extra("astrbot_original_message_str", "")
            )
        if request_kind is None:
            return

        from app.astrbot_menu import _raw_event_for_reset

        raw_event = _raw_event_for_reset(self, event)
        if not await self._legacy_event_allowed(raw_event):
            # 即使被限制，也不要让后续 AstrBot 默认帮助处理器绕过旧规则。
            event.should_call_llm(False)
            event.stop_event()
            return

        image_groups, text_groups = build_unified_menu_groups(self.context, event)
        from app.services.help_menu import concise_text_menu, render_help_menu

        event.should_call_llm(False)
        event.stop_event()
        if request_kind == "text":
            yield event.make_result().message(concise_text_menu(text_groups))
            return

        try:
            encoded = await render_help_menu(
                self.runtime._core.settings,
                image_groups=image_groups,
                text_groups=text_groups,
            )
        except Exception:
            _LOGGER.exception("绘制菜单图片失败")
            yield event.make_result().message("菜单图片暂时生成不了，可以发“文字版菜单”。")
            return

        normalized = self.runtime._core._qq_safe_image_variant(encoded) or encoded
        normalized = normalized.removeprefix("base64://")
        try:
            # 直接等发送结果，才能接住协议端超时；yield 的发送发生在函数外。
            await event.send(event.make_result().base64_image(normalized))
        except Exception as exc:  # noqa: BLE001 -- 各平台的发送错误类型不同。
            _LOGGER.warning("菜单图片发送未完成（%s）", type(exc).__name__)
            yield event.make_result().message("菜单图片发送失败或超时，可以发“文字版菜单”。")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-90)
    async def on_anime_image(self, event: AstrMessageEvent):
        """明确要随机图片时交给 AstrBot 图片插件。"""
        if _is_stopped(event) or _result_has_reply(_get_result_or_none(event)):
            return
        if getattr(event, "_has_send_oper", False):
            return
        from app.astrbot_plugin_images import forward_anime_image

        results = await forward_anime_image(event, self.context)
        if results is None:
            return
        event.should_call_llm(False)
        try:
            async for result in results:
                yield result
        finally:
            event.stop_event()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-100)
    async def on_wechat_message(self, event: AstrMessageEvent) -> None:
        """微信私聊和群聊共用业务功能，账号和会话各自隔离。"""
        get_name = getattr(event, "get_platform_name", None)
        name = str(get_name() if callable(get_name) else "").casefold()
        if not any(tag in name for tag in ("weixin", "wechat", "wecom")):
            return
        if (
            _is_stopped(event)
            or _result_has_reply(_get_result_or_none(event))
            or getattr(event, "_has_send_oper", False)
        ):
            return
        from app.astrbot_wechat import WeChatTransport

        platform_id = str(event.get_platform_id())
        account_id = str(getattr(getattr(event, "platform", None), "account_id", "") or "")
        key = (platform_id, account_id)
        bridge = self._wechat_bridges.get(key)
        if bridge is None:
            bridge = WeChatTransport(
                self.context, platform_id=platform_id,
                account_id=account_id or None, platform_name=name,
            )
            self._wechat_bridges[key] = bridge
        raw_event = bridge.bind_event(event)
        if raw_event is None:
            return
        if self.runtime is None:
            raise RuntimeError("聊天机器人插件尚未初始化")
        if self.legacy_chat:
            event.should_call_llm(False)
        from app.astrbot_llm import model_session

        with model_session(getattr(event, "unified_msg_origin", None)):
            result = await self.runtime.handle_event(raw_event, bridge.action_call)
        if result.get("handled"):
            event.should_call_llm(False)
            event.stop_event()

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=-100)
    async def on_onebot_message(self, event: AstrMessageEvent) -> None:
        """把原始 OneBot 消息交给现有分发器。

        默认优先级为 0；负优先级让常规处理器先运行。
        """
        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if not isinstance(raw_message, Mapping):
            if self.legacy_chat:
                event.should_call_llm(False)
            return
        raw_event = dict(raw_message)
        if raw_event.get("post_type") != "message" or raw_event.get("message_type") != "group":
            return

        stopped = _is_stopped(event)
        prior_result = _get_result_or_none(event)
        prior_reply = _result_has_reply(prior_result)
        prior_send = bool(getattr(event, "_has_send_oper", False))

        # 旧聊天模式由本插件负责；已有结果或回复时避免再次回答。
        if self.legacy_chat or stopped or prior_reply or prior_send:
            event.should_call_llm(False)

        if stopped or prior_reply or prior_send:
            return

        if self.runtime is None:
            raise RuntimeError("聊天机器人插件尚未初始化")

        # 保留原始消息段，避免丢失 @、回复、图片、音频和视频。
        # 不把机器人自己的回显再次派发。
        user_id = raw_event.get("user_id")
        self_id = raw_event.get("self_id")
        if user_id is not None and self_id is not None and str(user_id) == str(self_id):
            event.should_call_llm(False)
            return

        bot_api = event.bot.api

        async def action_call(action: str, **params: Any) -> dict[str, Any]:
            """通过 AstrBot 的 OneBot 客户端调用协议端 API。"""
            if self_id is not None:
                params["self_id"] = str(self_id)
            return await bot_api.call_action(action, **params)

        from app.astrbot_llm import model_session

        with model_session(getattr(event, "unified_msg_origin", None)):
            result = await self.runtime.handle_event(raw_event, action_call)
        if result.get("handled"):
            event.should_call_llm(False)
            event.stop_event()


def _result_has_reply(result: Any | None) -> bool:
    """空结果不算已回复。"""
    if isinstance(result, str):
        return bool(result.strip())
    return bool(getattr(result, "chain", None))
