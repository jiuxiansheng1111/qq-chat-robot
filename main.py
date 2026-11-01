"""AstrBot 插件入口，复用现有 QQ 机器人运行时。"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import weakref
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


class _QQChatEarlyFilter(filter.CustomFilter):
    """WakingCheck 先执行过滤器，命令识别必须在这里而非 handler 中完成。"""

    plugin_ref: Any = None

    def filter(self, event: AstrMessageEvent, cfg: Any) -> bool:
        plugin = self.plugin_ref() if self.plugin_ref is not None else None
        if plugin is None or plugin.legacy_chat or plugin.runtime is None:
            return False
        router = plugin._get_group_router()
        if not router.in_scope(event):
            return False
        from app.astrbot_group_chat import registered_command_names

        return router.prepare_event(event, registered_command_names(event))


class _QQChatMahjongFilter(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: Any) -> bool:
        plugin = _QQChatEarlyFilter.plugin_ref() if _QQChatEarlyFilter.plugin_ref else None
        if plugin is None or plugin.runtime is None:
            return False
        from app.astrbot_mahjong import request_args

        core = getattr(plugin.runtime, "_core", None)
        state = core.app.state if core is not None else None
        return request_args(event, state) is not None


class QQChatPlugin(Star):
    """把 OneBot v11 消息交给本地机器人运行时。"""

    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.legacy_chat = bool(self.config.get("legacy_chat", True))
        self.runtime: Any | None = None
        self._wechat_bridges: dict[tuple[str, str], Any] = {}
        self._menu_ready = False
        self._menu_prepare_lock = asyncio.Lock()
        self._minecraft_bridge: Any | None = None
        self._mahjong_panel: Any | None = None
        self._group_router: Any | None = None
        self._reply_guard: Any | None = None
        self._reply_repair: Any | None = None
        _QQChatEarlyFilter.plugin_ref = weakref.ref(self)

    def _get_group_router(self) -> Any:
        if self._group_router is None:
            from app.astrbot_group_chat import GroupChatRouter

            self._group_router = GroupChatRouter(self.config)
        return self._group_router

    def _get_reply_guard(self) -> Any:
        if self._reply_guard is None:
            from app.astrbot_reply_guard import GroupReplyGuard

            self._reply_guard = GroupReplyGuard(
                cache_path=_find_project_root() / "data/astrbot/group-reply-guard.json",
            )
        return self._reply_guard

    def _get_reply_repair(self) -> Any:
        if self._reply_repair is None:
            from app.astrbot_reply_repair import DirectReplyRepair

            self._reply_repair = DirectReplyRepair(self._get_reply_guard())
        return self._reply_repair

    @filter.custom_filter(_QQChatEarlyFilter, priority=1000000)
    async def route_native_group_message(self, event: AstrMessageEvent) -> Any | None:
        """在其他消息处理器前检查原群开关，并选择本条人类消息是否接话。"""
        if self.legacy_chat or self.runtime is None or _is_stopped(event):
            return
        router = self._get_group_router()
        if not router.in_scope(event):
            return
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if router.ignored_sender(event) or not await self._legacy_event_allowed(raw):
            # 保留旧的群管理员重新开启入口，其他消息继续沿用原有 ACL。
            sender = raw.get("sender", {}) if isinstance(raw, Mapping) else {}
            if not isinstance(sender, Mapping):
                sender = {}
            can_reenable = (
                not router.ignored_sender(event)
                and event.get_message_str() in {"bot on", "机器人开启", "/bot on", "/机器人开启"}
                and sender.get("role") in {"admin", "owner"}
            )
            if not can_reenable:
                event.should_call_llm(False)
                event.stop_event()
                return
        from app.astrbot_plugin_status import private_companion_photo_hint

        hint = private_companion_photo_hint(self.context, event)
        if hint:
            event.should_call_llm(False)
            event.stop_event()
            return event.make_result().message(hint)
        router.observe_event(event)

    @filter.custom_filter(_QQChatMahjongFilter, priority=1000001)
    async def on_mahjong_review(self, event: AstrMessageEvent):
        if self.runtime is None or _is_stopped(event):
            return
        from app.astrbot_mahjong import deliver_native_review, request_args

        args = request_args(event, self.runtime._core.app.state)
        if args is None:
            return
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        event.should_call_llm(False)
        event.stop_event()
        if not await self._legacy_event_allowed(raw):
            return
        # stop_event terminates AstrBot's scheduler at the next yield. Complete
        # this owned reply through event.send instead of yielding a pending job.
        await deliver_native_review(self.runtime, event, args)

    @filter.after_message_sent(priority=-300000)
    async def finish_native_group_reply(self, event: AstrMessageEvent) -> None:
        """发送后释放本群的插话占位，不安排无人跟进时的后续任务。"""
        if not self.legacy_chat and self._group_router is not None:
            self._group_router.finish_event(event, sent=bool(getattr(event, "_has_send_oper", False)))
        if not self.legacy_chat and self._reply_guard is not None:
            self._reply_guard.mark_sent(event)

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

        from app.astrbot_chatgpt_provider import register_chatgpt_provider
        from app.astrbot_flash_provider import register_flash_provider

        register_flash_provider()
        register_chatgpt_provider()

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
        from app.services.mahjong.account_panel import AccountPanel

        self._mahjong_panel = AccountPanel(project_root)
        try:
            await self._mahjong_panel.start()
        except OSError:
            _LOGGER.warning("雀魂账号设置页未启动：127.0.0.1:6190 已被占用")
        # AstrBot unloads its plugin namespace, but the shared app package remains.
        # Refresh the game bridge and group report routing after the previous instance has closed;
        # preserve the existing LLM modules, bound providers and configuration.
        for module_name in (
            "app.astrbot_group_chat", "app.astrbot_group_reports",
            "app.services.anime_image_policy",
            "app.astrbot_plugin_images",
            "app.minecraft_voice_runtime", "app.minecraft_voice", "app.minecraft_music_audio",
            "app.minecraft_music", "app.minecraft_bridge",
        ):
            module = sys.modules.get(module_name)
            if module is not None:
                importlib.reload(module)
        from app.minecraft_bridge import MinecraftBridge

        bridge = MinecraftBridge(
            runtime._core.app.state.llm, project_root, settings=runtime._core.settings,
        )
        self._minecraft_bridge = bridge
        try:
            await bridge.start()
        except OSError as exc:
            bridge.status = "port_unavailable"
            _LOGGER.error(
                "Minecraft 文本桥无法绑定 127.0.0.1:8012（%s）；未修改现有监听者",
                type(exc).__name__,
            )
        except Exception as exc:
            bridge.status = "unavailable"
            _LOGGER.exception("Minecraft 文本桥启动失败（%s）", type(exc).__name__)

    async def _prepare_local_menus(self) -> None:
        if self.runtime is None:
            return
        from app.astrbot_menu_files import prepare_default_menu_files

        async with self._menu_prepare_lock:
            try:
                count = await prepare_default_menu_files(self.context, self.runtime._core.settings)
                _LOGGER.info("本地菜单 JPG 已准备（%s 个平台/权限版本）", count)
            except Exception as exc:  # noqa: BLE001 -- 外部插件的菜单错误不影响启动。
                _LOGGER.warning("准备本地菜单失败（%s）", type(exc).__name__)

    @filter.on_astrbot_loaded()
    async def prepare_menus_after_start(self) -> None:
        self._menu_ready = True
        await self._prepare_local_menus()

    @filter.on_plugin_loaded()
    async def update_menus_after_plugin_loaded(self, metadata: Any) -> None:
        if self._menu_ready:
            await self._prepare_local_menus()

    @filter.on_plugin_unloaded()
    async def update_menus_after_plugin_unloaded(self, metadata: Any) -> None:
        if self._menu_ready:
            await self._prepare_local_menus()

    async def terminate(self) -> None:
        """卸载插件时释放队列和后台任务。"""
        self._menu_ready = False
        runtime, self.runtime = self.runtime, None
        try:
            panel, self._mahjong_panel = self._mahjong_panel, None
            if panel is not None:
                await panel.close()
            bridge, self._minecraft_bridge = self._minecraft_bridge, None
            if bridge is not None:
                try:
                    await bridge.close()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("关闭 Minecraft 文本桥失败（%s）", type(exc).__name__)
            if runtime is not None:
                await runtime.close()
        finally:
            self._wechat_bridges.clear()
            self._group_router = None
            if _QQChatEarlyFilter.plugin_ref is not None and _QQChatEarlyFilter.plugin_ref() is self:
                _QQChatEarlyFilter.plugin_ref = None

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

    @filter.on_llm_request(priority=-100)
    async def prepare_native_chat(self, event: AstrMessageEvent, request: Any) -> None:
        """普通聊天交给原生插件流程，保留每用户的角色与音色设置。"""
        if self.legacy_chat or self.runtime is None:
            return
        from app.astrbot_chat import prepare_native_chat

        try:
            await prepare_native_chat(self.runtime, event, request)
        except Exception as exc:  # noqa: BLE001 -- 上下文补充失败不能中断其他插件聊天。
            _LOGGER.warning("补充原生聊天角色上下文失败（%s）", type(exc).__name__)
        from app.astrbot_identity import identity_question_kind, identity_request_prompt

        platform_getter = getattr(event, "get_platform_name", None)
        text_getter = getattr(event, "get_message_str", None)
        platform = str(platform_getter() if callable(platform_getter) else "").casefold()
        text = str(text_getter() if callable(text_getter) else "")
        if platform in {"aiocqhttp", "onebot", "onebot_v11"} and identity_question_kind(text) == "technical":
            model_name = None
            try:
                provider = await self.context.get_using_provider_async(event.unified_msg_origin)
                if provider is not None:
                    model_name = provider.get_model()
            except Exception as exc:  # noqa: BLE001 -- 只读型号失败时保留“未知”，不猜配置。
                _LOGGER.debug("读取当前模型标识未完成（%s）", type(exc).__name__)
            request.system_prompt = str(getattr(request, "system_prompt", "") or "") + "\n" + identity_request_prompt(text, model_name)

    @filter.command("主动消息")
    async def proactive_group_settings(self, event: AstrMessageEvent) -> Any:
        """查看本群主动消息开关和频率。"""
        from app.astrbot_plugin_status import proactive_group_status

        event.should_call_llm(False)
        event.stop_event()
        return event.make_result().message(proactive_group_status(self.context, event))

    @filter.command("群报", alias={"群总结", "群日报"})
    async def calendar_group_reports(self, event: AstrMessageEvent, date_text: str = ""):
        """群报默认昨日＋今日；群报 昨天 / 群报 YYYY-MM-DD 仅补发指定一天。"""
        event.should_call_llm(False)
        event.stop_event()
        router = self._get_group_router()
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if (self.runtime is None or not router.in_scope(event) or router.ignored_sender(event)
                or not await self._legacy_event_allowed(raw)):
            await event.send(event.plain_result("请在已启用的本群里请求群报。"))
            return
        from astrbot.api.message_components import Image, Plain

        from app.astrbot_group_reports import request_group_reports

        # stop_event ends AstrBot's generator pipeline at the next yield. Keep
        # this command's preparation and final report in its owned send flow.
        if date_text:
            await event.send(event.plain_result("我去整理这一天的群报～如果是今天，统计到上一完整分钟。"))
            batch = await request_group_reports(self.context, event, date_text=date_text)
        else:
            await event.send(event.plain_result("我去整理昨天和今天的两份群报～今日统计到上一完整分钟。"))
            batch = await request_group_reports(self.context, event)
        if not batch.items:
            await event.send(event.plain_result(batch.message or "群报暂时没整理好，稍后再试一下。"))
            return
        chain = []
        for item in batch.items:
            chain.append(Plain(item.header + ("\n" + item.text if item.text else "") + "\n"))
            if item.image_path:
                chain.append(Image.fromFileSystem(item.image_path))
        await event.send(event.chain_result(chain))

    @filter.llm_tool(name="qqchat_web_search")
    async def qqchat_web_search(
        self, event: AstrMessageEvent, query: str, recency: str = "noLimit",
    ) -> str:
        """联网查证近期新闻、新游戏、版本更新、陌生网梗和不确定事实，返回来源与日期。

        Args:
            query(string): 精简的搜索关键词，最多70字；补全当前话题的实体名，不要发送聊天记录或隐私资料。
            recency(string): 时间范围：oneDay一天内，oneWeek一周内，oneMonth一月内，oneYear一年内，noLimit不限。今天新闻选oneDay，最近消息选oneWeek，梗的来源或游戏知识可选noLimit。
        """
        if not self.config.get("web_search_enabled", True):
            return '{"status":"disabled","message":"联网搜索已关闭。"}'
        from app.astrbot_search import native_web_search

        settings = getattr(getattr(self.runtime, "_core", None), "settings", None)
        return await native_web_search(self.context, event, query, recency, settings=settings)

    @filter.on_llm_request(priority=-270000)
    async def prepare_group_reply(self, event: AstrMessageEvent, request: Any) -> None:
        """其他插件补完上下文后，落实普通群消息的检索和表达要求。"""
        if self.legacy_chat or self.runtime is None:
            return
        from app.astrbot_search import prepare_group_search_context

        settings = getattr(getattr(self.runtime, "_core", None), "settings", None)
        await prepare_group_search_context(
            self.context, event, request, settings=settings,
            enabled=bool(self.config.get("web_search_enabled", True)),
        )
        self._get_group_router().prepare_request(event, request)

    @filter.on_llm_response(priority=-300000)
    async def repair_direct_group_reply(self, event: AstrMessageEvent, response: Any) -> None:
        """明确点名的答复跑偏时有界纠正，避免被避重拦下后毫无反馈。"""
        if self.legacy_chat or self.runtime is None:
            return
        from app.astrbot_identity import apply_character_identity

        if apply_character_identity(event, response, self._get_reply_guard()):
            return
        router = self._get_group_router()
        if not router.in_scope(event) or not router.allow_reply(event):
            return
        from app.astrbot_silence import is_silence_control_text

        if (event.get_extra("qqchat_group_reply_reason") == "interject"
                and is_silence_control_text(str(getattr(response, "completion_text", "") or ""))):
            response.completion_text = ""
            chain = getattr(response, "result_chain", None)
            if chain is not None:
                chain.chain = []
            router.suppress_reply(event)
            event.should_call_llm(False)
            event.stop_event()
            return
        outcome = await self._get_reply_repair().repair_response(self.context, event, response)
        if outcome == "notice_cooldown":
            router.finish_event(event)
            event.should_call_llm(False)
            event.stop_event()

    @filter.on_decorating_result(priority=100)
    async def suppress_repeated_group_reply(self, event: AstrMessageEvent) -> None:
        """先检查聊天原文，再交给语音和平台装饰；被拒绝的回复不会发出。"""
        if self.legacy_chat or self.runtime is None:
            return
        router = self._get_group_router()
        if not router.in_scope(event) and not router.is_idle_event(event):
            return
        result = event.get_result()
        if result is None or not result.is_llm_result():
            return
        if not router.allow_reply(event):
            if self._reply_guard is not None:
                self._reply_guard.discard(event)
            event.clear_result()
            event.stop_event()
            router.finish_event(event)
            _LOGGER.info("群聊已要求安静，取消此前排队的聊天回复。")
            return
        reason = self._get_reply_guard().prepare_result(event)
        if reason:
            router.suppress_reply(event)
            _LOGGER.info("群聊发送前拦截无信息或重复回复：%s。", reason)

    @filter.on_decorating_result(priority=-100)
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
        # QQ 群只收掉整条回复末尾的句号，保留正文、问号、感叹号和省略号。
        if event.get_group_id():
            from app.services.chat_punctuation import without_final_period

            for component in reversed(getattr(result, "chain", ()) or ()):
                if isinstance(component, Plain) and component.text.strip():
                    component.text = without_final_period(component.text)
                    break
        if not self.legacy_chat and self.runtime is not None:
            from app.astrbot_chat import decorate_native_voice

            try:
                await decorate_native_voice(self.runtime, event)
            except Exception as exc:  # noqa: BLE001 -- 语音失败必须保留原生文字结果。
                _LOGGER.warning("原生聊天语音处理失败，保留原结果（%s）", type(exc).__name__)

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

        image_groups, text_groups = build_unified_menu_groups(self.context, event, self.runtime._core.settings)
        from app.services.help_menu import (
            COMPACT_MENU_SUBTITLE,
            COMPACT_MENU_TITLE,
            concise_text_menu,
            prepare_help_menu_file,
        )

        event.should_call_llm(False)
        event.stop_event()
        if request_kind == "text":
            yield event.make_result().message(concise_text_menu(
                text_groups, title=COMPACT_MENU_TITLE, subtitle=COMPACT_MENU_SUBTITLE,
            ))
            return

        try:
            from app.astrbot_menu_files import choose_event_menu_file

            menu_path = await prepare_help_menu_file(
                self.runtime._core.settings,
                image_groups=image_groups,
                text_groups=text_groups,
                title=COMPACT_MENU_TITLE,
                subtitle=COMPACT_MENU_SUBTITLE,
            )
            menu_path = await choose_event_menu_file(
                self.context, event, menu_path, image_groups, text_groups,
                settings=self.runtime._core.settings,
            )
        except Exception:
            _LOGGER.exception("绘制菜单图片失败")
            yield event.make_result().message("菜单图片暂时生成不了，可以发“文字版菜单”。")
            return

        from app.astrbot_menu_files import menu_send_error_info, send_menu_file

        try:
            await send_menu_file(event, menu_path)
        except Exception as exc:  # noqa: BLE001 -- 各平台的发送错误类型不同。
            _LOGGER.warning("本地菜单发送未完成（%s，返回码 %s，%s）", *menu_send_error_info(exc))
            yield event.make_result().message("菜单图片发送失败或超时，可以发“文字版菜单”。")

    @filter.command("来一份二次元图片", alias={"来份二次元图片", "来一张二次元图片", "来张二次元图片"})
    async def anime_image_command(self, event: AstrMessageEvent):
        """从已启用图片源取一张二次元图片。"""
        await self.on_anime_image(event)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-90)
    async def on_anime_image(self, event: AstrMessageEvent):
        """明确要随机图片时交给 AstrBot 图片插件。"""
        if _is_stopped(event) or _result_has_reply(_get_result_or_none(event)):
            return
        if getattr(event, "_has_send_oper", False):
            return
        if self.runtime is None:
            return
        from app.astrbot_menu import _raw_event_for_reset

        if not await self._legacy_event_allowed(_raw_event_for_reset(self, event)):
            return
        from app.astrbot_plugin_images import forward_anime_image

        results = await forward_anime_image(event, self.context)
        if results is None:
            return
        event.should_call_llm(False)
        try:
            async for result in results:
                # The upstream handler stops the event before yielding. Send
                # its result here so the scheduler cannot discard the image.
                await event.send(result)
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
        elif not self.legacy_chat and result.get("native_chat_requested"):
            event.is_at_or_wake_command = True

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

        from app.astrbot_group_chat import normalized_runtime_event

        get_extra = getattr(event, "get_extra", None)
        if callable(get_extra) and get_extra("qqchat_skip_local_command", False):
            return
        raw_event = normalized_runtime_event(event, raw_event)
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
        elif not self.legacy_chat and result.get("native_chat_requested"):
            event.is_at_or_wake_command = True


def _result_has_reply(result: Any | None) -> bool:
    """空结果不算已回复。"""
    if isinstance(result, str):
        return bool(result.strip())
    return bool(getattr(result, "chain", None))
