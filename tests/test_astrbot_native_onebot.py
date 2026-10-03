"""用真实 AstrBot/aiocqhttp 事件对象验证 OneBot 插件入口。"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar

import pytest


def _find_project_root() -> Path:
    configured = os.environ.get("QQ_CHATBOT_PROJECT_ROOT")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend(Path(__file__).resolve().parents)
    for candidate in candidates:
        root = candidate.resolve()
        if (root / "main.py").is_file() and (root / "app" / "main.py").is_file():
            return root
    pytest.skip("找不到项目根目录；复制到仓库 tests/ 下或设置 QQ_CHATBOT_PROJECT_ROOT")


def _load_astrbot_types(root: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    runtime_root = root / "data" / "astrbot" / "runtime" / "tool-envs" / "astrbot"
    candidates = [
        runtime_root / "Lib" / "site-packages",
        runtime_root / "lib" / "python3.12" / "site-packages",
    ]
    for site_packages in candidates:
        if site_packages.is_dir():
            monkeypatch.syspath_prepend(str(site_packages))

    try:
        import astrbot
        from astrbot.api.message_components import At, Image, Plain
        from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
        from astrbot.core.platform.message_type import MessageType
        from astrbot.core.platform.platform_metadata import PlatformMetadata
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
            AiocqhttpMessageEvent,
        )
    except (ImportError, SyntaxError) as exc:
        pytest.skip(f"AstrBot/aiocqhttp 测试依赖不可用：{type(exc).__name__}")

    version = getattr(astrbot, "__version__", None)
    assert version == "4.28.2", f"此原生事件用例针对 AstrBot 4.28.2，当前为 {version!r}"
    return SimpleNamespace(
        At=At,
        Image=Image,
        Plain=Plain,
        AstrBotMessage=AstrBotMessage,
        MessageMember=MessageMember,
        MessageType=MessageType,
        PlatformMetadata=PlatformMetadata,
        AiocqhttpMessageEvent=AiocqhttpMessageEvent,
    )


class RecordingApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_action(self, action: str, **params: Any) -> dict[str, Any]:
        self.calls.append((action, params))
        return {"status": "ok", "retcode": 0, "data": {}}


class FakeChatRuntime:
    instances: ClassVar[list[FakeChatRuntime]] = []

    def __init__(self, *, project_root: Path, legacy_chat: bool) -> None:
        self.project_root = project_root
        self.legacy_chat = legacy_chat
        self.events: list[dict[str, Any]] = []
        self.closed = False
        self.instances.append(self)

    async def initialize(self) -> None:
        return None

    async def handle_event(self, raw_event: dict[str, Any], action_call):
        self.events.append(raw_event)
        await action_call(
            "send_group_msg",
            group_id=raw_event["group_id"],
            message=raw_event["message"],
        )
        return {"handled": True, "source": "isolated-test"}

    async def close(self) -> None:
        self.closed = True


def _load_plugin(root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(root))
    runtime_module = ModuleType("app.astrbot_runtime")
    runtime_module.ChatRuntime = FakeChatRuntime  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.astrbot_runtime", runtime_module)

    spec = importlib.util.spec_from_file_location(
        "_native_onebot_main_under_test", root / "main.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _make_group_event(types: SimpleNamespace) -> tuple[Any, RecordingApi, dict[str, Any]]:
    message_segments = [
        {"type": "at", "data": {"qq": "99999"}},
        {"type": "text", "data": {"text": "看看这张图 "}},
        {"type": "image", "data": {"file": "https://cdn.invalid/fixture.png"}},
    ]
    raw_message = {
        "time": 123,
        "self_id": 99999,
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": 987654321,
        "group_id": 12345,
        "user_id": 12345,
        "anonymous": None,
        "message": message_segments,
        "raw_message": "[CQ:at,qq=99999]看看这张图 [CQ:image,file=fixture.png]",
        "font": 0,
        "sender": {"user_id": 12345, "nickname": "测试用户", "role": "member"},
    }

    message = types.AstrBotMessage()
    message.type = types.MessageType.GROUP_MESSAGE
    message.self_id = "99999"
    message.session_id = "12345"
    message.message_id = "987654321"
    message.sender = types.MessageMember(user_id="12345", nickname="测试用户")
    message.message = [
        types.At(qq="99999"),
        types.Plain("看看这张图 "),
        types.Image.fromURL("https://cdn.invalid/fixture.png"),
    ]
    message.timestamp = 123
    message.raw_message = raw_message

    metadata = types.PlatformMetadata(
        name="aiocqhttp", description="隔离测试", id="qq-chatrobot-local"
    )
    api = RecordingApi()
    event = types.AiocqhttpMessageEvent(
        message_str="看看这张图",
        message_obj=message,
        platform_meta=metadata,
        session_id="12345",
        bot=SimpleNamespace(api=api),
    )
    return event, api, raw_message


@pytest.mark.asyncio
async def test_native_onebot_group_event_dispatch_and_preserve_segments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    root = _find_project_root()
    types = _load_astrbot_types(root, monkeypatch)
    plugin_module = _load_plugin(root, monkeypatch)
    FakeChatRuntime.instances.clear()

    plugin = plugin_module.QQChatPlugin(SimpleNamespace(), {"legacy_chat": True})
    await plugin.initialize()
    event, api, raw_message = _make_group_event(types)
    try:
        await plugin.on_onebot_message(event)

        assert len(FakeChatRuntime.instances) == 1
        runtime = FakeChatRuntime.instances[0]
        assert runtime.project_root == root
        assert runtime.legacy_chat is True
        assert len(runtime.events) == 1
        dispatched = runtime.events[0]
        assert dispatched["message_type"] == "group"
        assert dispatched["group_id"] == 12345
        assert dispatched["user_id"] == 12345
        assert dispatched["message"] is raw_message["message"]
        assert dispatched["message"] == raw_message["message"]

        assert api.calls == [
            (
                "send_group_msg",
                {
                    "group_id": 12345,
                    "message": raw_message["message"],
                    "self_id": "99999",
                },
            )
        ]
        assert event.is_stopped() is True
    finally:
        await plugin.terminate()
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_native_event_already_handled_by_another_plugin_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    root = _find_project_root()
    types = _load_astrbot_types(root, monkeypatch)
    plugin_module = _load_plugin(root, monkeypatch)
    FakeChatRuntime.instances.clear()

    plugin = plugin_module.QQChatPlugin(SimpleNamespace(), {"legacy_chat": True})
    await plugin.initialize()
    event, api, _raw_message = _make_group_event(types)
    event.set_result("外部插件已处理")
    prior_result = event.get_result()
    try:
        await plugin.on_onebot_message(event)

        assert FakeChatRuntime.instances[0].events == []
        assert api.calls == []
        assert event.get_result() == prior_result
        assert event.is_stopped() is False
    finally:
        await plugin.terminate()
