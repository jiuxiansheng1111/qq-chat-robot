"""把原来的群聊功能接到 AstrBot，复用本机配置和数据。"""

from __future__ import annotations

import importlib
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.config import Settings
from app.services.onebot_routing import (
    current_onebot_self_id,
    register_action_client,
    set_current_onebot_self_id,
    track_replies,
    unregister_action_client,
)

_LOCAL_PATH_FIELDS = (
    "database_path", "persona_prompt_file", "persona_examples_file",
    "ultraman_image_cache_dir", "anime_image_cache_dir", "murasame_asset_dir",
    "netease_member_token_path", "singing_python", "singing_seed_root",
    "singing_ffmpeg_path", "singing_ffprobe_path", "help_menu_background_path",
)
_BLOCKING_REASONS = frozenset({
    "group_disabled", "user_blocked", "blocked", "plugin_disabled", "duplicate_event",
    "user_ingress_rate_limited", "group_ingress_rate_limited",
})
_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


def project_settings(project_root: Path) -> Settings:
    """相对路径按项目目录算，不改 AstrBot 的工作目录。"""
    settings = Settings(_env_file=project_root / ".env")
    paths = {}
    for field in _LOCAL_PATH_FIELDS:
        value = getattr(settings, field)
        if value:
            path = Path(value).expanduser()
            paths[field] = str((path if path.is_absolute() else project_root / path).resolve())
    return settings.model_copy(update=paths)


class ChatRuntime:
    def __init__(self, project_root: Path, *, legacy_chat: bool = True):
        self.project_root = Path(project_root).resolve()
        self.legacy_chat = legacy_chat
        self._core = None
        self._previous_settings = None
        self._registered_ids: set[str] = set()
        self._initialized = False
        self._previous_proxy: dict[str, str | None] = {}
        self._installed_proxy: dict[str, str | None] = {}

    async def initialize(self) -> None:
        if self._initialized:
            return
        self._core = importlib.import_module("app.main")
        self._previous_settings = self._core.settings
        self._core.settings = project_settings(self.project_root)
        self._previous_proxy = {key: os.environ.get(key) for key in _PROXY_KEYS}
        try:
            await self._core.initialize_runtime(self._core.app)
        except BaseException:
            self._installed_proxy = {key: os.environ.get(key) for key in _PROXY_KEYS}
            with suppress(Exception):
                await self._core.close_runtime(self._core.app)
            self._core.settings = self._previous_settings
            self._restore_proxy()
            raise
        self._installed_proxy = {key: os.environ.get(key) for key in _PROXY_KEYS}
        self._initialized = True

    def _restore_proxy(self) -> None:
        for key, previous in self._previous_proxy.items():
            if os.environ.get(key) != self._installed_proxy.get(key):
                continue
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous

    async def handle_event(
        self,
        raw_event: dict,
        action_call: Callable[..., Awaitable[Any]],
    ) -> dict:
        if not self._initialized or self._core is None:
            raise RuntimeError("聊天机器人插件尚未初始化")
        if raw_event.get("post_type") != "message" or raw_event.get("message_type") != "group":
            return {"ok": True, "ignored": True, "handled": False}
        self_id = str(raw_event.get("self_id") or "").strip()
        if not self_id:
            return {"ok": True, "ignored": True, "handled": False}
        register_action_client(self_id, action_call)
        self._registered_ids.add(self_id)
        previous_self_id = current_onebot_self_id()
        try:
            with track_replies() as replies:
                result = await self._core.dispatch_onebot_event(
                    raw_event, SimpleNamespace(app=self._core.app), allow_chat=self.legacy_chat,
                )
        finally:
            set_current_onebot_self_id(previous_self_id)
        result = dict(result or {})
        result["handled"] = bool(
            replies.attempted or result.get("handled") or result.get("reason") in _BLOCKING_REASONS
        )
        result["reply_sent"] = bool(replies.succeeded)
        return result

    async def close(self) -> None:
        if not self._initialized or self._core is None:
            return
        try:
            await self._core.close_runtime(self._core.app)
        finally:
            for self_id in self._registered_ids:
                unregister_action_client(self_id)
            self._registered_ids.clear()
            self._core.settings = self._previous_settings
            self._restore_proxy()
            self._initialized = False
