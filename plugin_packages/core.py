"""AstrBot host for the independently installable QQChat features."""
from __future__ import annotations

import asyncio
import logging
import sys
import weakref
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .support import HostSupport
from .runtime_files import prepare_runtime

_LOG = logging.getLogger(__name__)


class InScope(filter.CustomFilter):
    current = None

    def filter(self, event, cfg):
        core = self.current() if self.current else None
        if not core or not core.runtime or core.bundled_active() or not core.parts(event):
            return False
        from app.astrbot_qq_persona import is_qq
        if not is_qq(event):
            return False
        if not event.get_group_id():
            return True
        from app.astrbot_group_chat import registered_command_names
        return core._get_group_router().prepare_event(event, registered_command_names(event))


class ConversationScope(filter.CustomFilter):
    def filter(self, event, cfg):
        host = InScope.current() if InScope.current else None
        if not host or not host.runtime or host.bundled_active():
            return False
        from app.astrbot_turn_features import routes
        return bool(routes(host, event))


class QQChatCorePlugin(HostSupport, Star):
    qqchat_core_api = 1

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.root = None
        self.legacy_chat = False
        self.runtime = None
        self._attached_parts = {}
        self._part_lock = asyncio.Lock()
        self._wechat_bridges = {}
        self._companion_turns = self._group_router = self._qq_memes = None
        self._reply_guard = self._reply_repair = self._music_listening = self._soundtry_jobs = None
        InScope.current = weakref.ref(self)

    def bundled_active(self):
        meta = self.context.get_registered_star("astrbot_plugin_qqchat")
        return bool(meta and meta.activated and meta.star_cls is not None)

    def _get_music_listening(self):
        from app.astrbot_listening import MusicListening, OPERATIONS_PATH
        from app.astrbot_soundtry import SoundtryJobs
        from app.services.listening_operations import ListeningOperations

        if self._soundtry_jobs is None:
            self._soundtry_jobs = SoundtryJobs()
            self._soundtry_jobs.on_delivered = self._archive_analysis
        if self._music_listening is None:
            self._music_listening = MusicListening(self._soundtry_jobs, operations=ListeningOperations(OPERATIONS_PATH))
        return self._music_listening

    async def _archive_analysis(self, event, text, source):
        if any(part.slug == "memory" for part in self.parts(event)):
            from app.services.owner_memory import record_assistant
            return await asyncio.to_thread(record_assistant, event, text, source=source)

    def parts(self, event=None):
        if self.bundled_active():
            return []
        allowed = getattr(event, "plugins_name", None)
        return [meta.star_cls for meta in self.context.get_all_stars()
                if meta.activated and getattr(meta.star_cls, "qqchat_part_api", None) == 1
                and (event is None or self._attached_parts.get(meta.star_cls.slug) is meta.star_cls)
                and (allowed is None or "*" in allowed or meta.name in allowed)]

    async def attach(self, part):
        async with self._part_lock:
            if self.runtime is None or self.bundled_active() or self._attached_parts.get(part.slug) is part:
                return
            previous = self._attached_parts.get(part.slug)
            if previous is not None:
                await self._release(previous)
            await self.install_files(part)
            hook = getattr(type(part), "start_part", None)
            if hook:
                await hook(part, self)
            self._attached_parts[part.slug] = part
            part._attached = self

    async def install_files(self, part):
        module = sys.modules[type(part).__module__]
        shipped = Path(module.__file__).resolve().parent / "runtime"
        if shipped.is_dir():
            await asyncio.to_thread(prepare_runtime, shipped, self.root, core=False)

    async def _release(self, part):
        try:
            from app.astrbot_turn_features import release
            await release(self, part.slug)
            hook = getattr(type(part), "stop_part", None)
            if hook:
                await hook(part, self)
        finally:
            part._attached = None
            self._attached_parts.pop(part.slug, None)

    async def detach(self, part):
        async with self._part_lock:
            if self._attached_parts.get(part.slug) is part:
                await self._release(part)

    async def initialize(self):
        if self.bundled_active():
            _LOG.info("整合版已启用，分件运行时待命。")
            return
        self.root = await asyncio.to_thread(prepare_runtime, Path(__file__).resolve().parent / "runtime",
                                            Path(StarTools.get_data_dir("astrbot_plugin_qqchat_core")))
        previous = sys.modules.get("app")
        if previous and Path(getattr(previous, "__file__", "") or ".").resolve().parent != self.root / "app":
            raise RuntimeError("当前进程已载入另一份 QQChat 运行时。请停用整合版并重启 AstrBot。")
        for part in self.parts():
            await self.install_files(part)
        root = str(self.root)
        if root not in sys.path:
            sys.path.insert(0, root)
        from app.astrbot_runtime import ChatRuntime
        from app.astrbot_chatgpt_provider import register_chatgpt_provider
        from app.astrbot_flash_provider import register_flash_provider
        from app.astrbot_vision_provider import register_vision_provider
        register_chatgpt_provider()
        register_flash_provider()
        register_vision_provider()
        runtime = ChatRuntime(project_root=self.root, legacy_chat=False)
        try:
            await runtime.initialize()
            await runtime.bind_astrbot_models(self.context)
        except BaseException:
            await runtime.close()
            raise
        self.runtime = runtime
        await self.sync()

    async def sync(self):
        if self.runtime is not None:
            for part in self.parts():
                try:
                    await self.attach(part)
                except Exception:
                    _LOG.exception("无法启用插件 %s", part.slug)

    @filter.on_astrbot_loaded()
    async def after_start(self):
        await self.sync()

    @filter.on_plugin_loaded()
    async def after_load(self, metadata):
        await self.sync()

    async def terminate(self):
        try:
            for part in list(self._attached_parts.values())[::-1]:
                try:
                    await self.detach(part)
                except Exception:
                    _LOG.exception("关闭分件失败 %s", part.slug)
            if self._companion_turns:
                await self._companion_turns.games.mc.close()
            if self._music_listening:
                await self._music_listening.close(preserve=True)
            if self._soundtry_jobs:
                await self._soundtry_jobs.close(preserve=True)
        finally:
            if self.runtime:
                await self.runtime.close()
                self.runtime = None

    @filter.custom_filter(ConversationScope, priority=1100000)
    async def conversation_features(self, event: AstrMessageEvent):
        from app.astrbot_turn_features import handle
        await handle(self, event)

    @filter.custom_filter(InScope, priority=1000000)
    async def on_natural_message(self, event: AstrMessageEvent):
        if self.runtime is None or event.is_stopped():
            return
        raw = getattr(event.message_obj, "raw_message", None)
        if not await self._legacy_event_allowed(raw):
            # Group administrators must still be able to re-enable a disabled group.
            role = ((raw or {}).get("sender") or {}).get("role")
            if role in {"admin", "owner"} and event.get_message_str().lstrip("/") in {"bot on", "机器人开启"}:
                return
            event.should_call_llm(False)
            event.stop_event()
            return
        parts = self.parts(event)
        router = self._get_group_router()
        if event.get_group_id() and router.ignored_sender(event):
            event.should_call_llm(False)
            event.stop_event()
            return
        if event.get_group_id() and any(p.slug == "persona" for p in parts):
            router.observe_event(event)
        for part in parts:
            hook = getattr(type(part), "message", None)
            if hook and not event.is_stopped() and await hook(part, self, event):
                router.finish_event(event)
                return
        turns = {turn for part in parts for turn in part.turns}
        if turns and not event.is_stopped() and await self._get_companion_turns().handle(self, event, features=turns):
            router.finish_event(event, sent=bool(getattr(event, "_has_send_oper", False)))

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-100)
    async def feature_command(self, event: AstrMessageEvent):
        if self.runtime is None or event.is_stopped() or getattr(event, "_has_send_oper", False):
            return
        enabled = {k for p in self.parts(event) for k in p.legacy}
        if not enabled:
            return
        from app.astrbot_menu import _raw_event_for_reset
        from app.astrbot_group_chat import normalized_runtime_event
        from app.modular_commands import matches
        raw = _raw_event_for_reset(self, event)
        if not raw or not matches(enabled, normalized_runtime_event(event, raw), self.runtime):
            return
        from app.astrbot_qq_persona import is_qq
        if is_qq(event):
            await self.on_onebot_message(event)
        else:
            await self.on_wechat_message(event)

    @filter.on_llm_request(priority=-280000)
    async def prepare_parts(self, event: AstrMessageEvent, request):
        if self.runtime is None:
            return
        parts = self.parts(event)
        enabled = {p.slug for p in parts}
        for part in sorted(parts, key=lambda p: getattr(type(p), "prepare_order", 50)):
            hook = getattr(type(part), "prepare", None)
            if hook:
                try:
                    await hook(part, self, event, request, enabled)
                except Exception as exc:
                    _LOG.warning("分件未能补充本轮上下文 %s (%s)", part.slug, type(exc).__name__)
