"""Isolated contract tests for the AstrBot adapter entry point."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MAIN = PROJECT_ROOT / "main.py"


def _load_plugin(monkeypatch: pytest.MonkeyPatch):
    """Load main.py against a tiny AstrBot API stub, so tests need no AstrBot install."""
    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []
    api = types.ModuleType("astrbot.api")
    api.__path__ = []
    event_api = types.ModuleType("astrbot.api.event")
    star_api = types.ModuleType("astrbot.api.star")

    class FakeStar:
        def __init__(self, context: Any) -> None:
            self.context = context

    class FakeEventMessageType:
        ALL = "all"
        GROUP_MESSAGE = "group"

    class FakePlatformAdapterType:
        AIOCQHTTP = "aiocqhttp"

    def decorator_factory(*args: Any, **kwargs: Any):
        def decorate(fn):
            calls = list(getattr(fn, "_test_filter_calls", []))
            calls.append((args, kwargs))
            fn._test_filter_calls = calls
            return fn

        return decorate

    class FakeFilter:
        EventMessageType = FakeEventMessageType
        PlatformAdapterType = FakePlatformAdapterType
        event_message_type = staticmethod(decorator_factory)
        platform_adapter_type = staticmethod(decorator_factory)
        on_astrbot_loaded = staticmethod(decorator_factory)
        on_plugin_loaded = staticmethod(decorator_factory)
        on_plugin_unloaded = staticmethod(decorator_factory)
        on_decorating_result = staticmethod(decorator_factory)

    event_api.AstrMessageEvent = type("AstrMessageEvent", (), {})
    event_api.filter = FakeFilter
    star_api.Context = type("Context", (), {})
    star_api.Star = FakeStar

    for name, module in (
        ("astrbot", astrbot),
        ("astrbot.api", api),
        ("astrbot.api.event", event_api),
        ("astrbot.api.star", star_api),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    module_name = "_test_astrbot_plugin_main"
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN_MAIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


class FakeEvent:
    def __init__(self, raw_event: dict[str, Any], *, result: Any = None, stopped=False):
        self.message_obj = SimpleNamespace(raw_message=raw_event)
        self.bot = SimpleNamespace(api=SimpleNamespace(call_action=self.call_action))
        self._result = result
        self._stopped = stopped
        self._has_send_oper = False
        self.llm_calls: list[bool] = []
        self.stop_calls = 0
        self.action_calls: list[tuple[str, dict[str, Any]]] = []

    async def call_action(self, action: str, **params: Any) -> dict[str, Any]:
        self.action_calls.append((action, params))
        return {"status": "ok"}

    def get_result(self):
        return self._result

    def is_stopped(self) -> bool:
        return self._stopped

    def should_call_llm(self, should_call: bool) -> None:
        self.llm_calls.append(should_call)

    def stop_event(self) -> None:
        self.stop_calls += 1
        self._stopped = True


class FakeRuntime:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.events: list[dict[str, Any]] = []
        self.action_response: dict[str, Any] | None = None

    async def handle_event(self, raw_event, action_call):
        self.events.append(raw_event)
        self.action_response = await action_call("send_group_msg", group_id=42, message="ok")
        return self.result


class FakeWechatEvent(FakeEvent):
    def __init__(
        self,
        *,
        platform_name="weixin_oc",
        platform_id="wx-adapter",
        account_id="wx-account",
        message_type="FriendMessage",
        session_id="peer-1",
        sender_id="peer-1",
        group_id="",
        message_id="wx-msg-1",
        text="你好",
        components=None,
        result=None,
        stopped=False,
        sent=False,
    ):
        super().__init__({}, result=result, stopped=stopped)
        self.platform_meta = SimpleNamespace(name=platform_name, id=platform_id)
        self.platform = SimpleNamespace(account_id=account_id)
        self.unified_msg_origin = f"{platform_id}:{message_type}:{session_id}"
        self.message_obj = SimpleNamespace(
            type=message_type,
            sender=SimpleNamespace(user_id=sender_id, nickname="用户甲"),
            group_id=group_id,
            message_id=message_id,
            timestamp=123,
            message=list(components or []),
        )
        self._has_send_oper = sent
        self._platform_name = platform_name
        self._platform_id = platform_id
        self._session_id = session_id
        self._sender_id = sender_id
        self._group_id = group_id
        self._text = text

    def get_platform_name(self):
        return self._platform_name

    def get_platform_id(self):
        return self._platform_id

    def get_self_id(self):
        return self._platform_id

    def get_message_type(self):
        return self.message_obj.type

    def get_session_id(self):
        return self._session_id

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return "用户甲"

    def get_group_id(self):
        return self._group_id

    def get_message_str(self):
        return self._text

    def get_messages(self):
        return self.message_obj.message


class CaptureWechatRuntime:
    def __init__(self, handled=False):
        self.handled = handled
        self.events = []
        self.action_calls = []
        self.closed = False

    async def handle_event(self, raw_event, action_call):
        self.events.append(raw_event)
        self.action_calls.append(action_call)
        return {"handled": self.handled}

    async def close(self):
        self.closed = True


class CaptureWechatContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))


@pytest.mark.asyncio
async def test_handled_onebot_event_is_passed_verbatim_and_stops_default_flow(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": False})
    raw_event = {
        "post_type": "message",
        "message_type": "group",
        "self_id": 111,
        "user_id": 222,
        "group_id": 42,
        "message": [
            {"type": "reply", "data": {"id": "7"}},
            {"type": "image", "data": {"file": "input.jpg"}},
            {"type": "text", "data": {"text": "describe this"}},
        ],
    }
    event = FakeEvent(raw_event)
    plugin.runtime = FakeRuntime({"handled": True})

    await plugin.on_onebot_message(event)

    assert plugin.runtime.events == [raw_event]
    assert plugin.runtime.action_response == {"status": "ok"}
    assert event.action_calls == [
        ("send_group_msg", {"group_id": 42, "message": "ok", "self_id": "111"})
    ]
    assert event.llm_calls == [False]
    assert event.stop_calls == 1


@pytest.mark.asyncio
async def test_unhandled_group_event_passes_to_astrbot_llm_when_legacy_chat_is_disabled(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": False})
    raw_event = {"post_type": "message", "message_type": "group", "message": []}
    event = FakeEvent(raw_event)
    plugin.runtime = FakeRuntime({"handled": False})

    await plugin.on_onebot_message(event)

    assert len(plugin.runtime.events) == 1
    assert event.llm_calls == []
    assert event.stop_calls == 0


@pytest.mark.asyncio
async def test_prior_result_is_not_dispatched_and_legacy_mode_suppresses_llm(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": True})
    event = FakeEvent(
        {"post_type": "message", "message_type": "group", "message": []},
        result=SimpleNamespace(chain=["已有回复"]),
    )
    plugin.runtime = FakeRuntime({"handled": True})

    await plugin.on_onebot_message(event)

    assert plugin.runtime.events == []
    assert event.llm_calls == [False]
    assert event.stop_calls == 0


@pytest.mark.asyncio
async def test_empty_result_does_not_block_group_dispatch_or_astrbot_llm(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": False})
    event = FakeEvent(
        {"post_type": "message", "message_type": "group", "message": []},
        result=SimpleNamespace(chain=[]),
    )
    plugin.runtime = FakeRuntime({"handled": False})

    await plugin.on_onebot_message(event)

    assert len(plugin.runtime.events) == 1
    assert event.llm_calls == []
    assert event.stop_calls == 0


@pytest.mark.asyncio
async def test_private_message_is_left_to_astrbot_even_if_handler_is_called(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": True})
    event = FakeEvent(
        {"post_type": "message", "message_type": "private", "message": []}
    )
    plugin.runtime = FakeRuntime({"handled": False})

    await plugin.on_onebot_message(event)

    assert plugin.runtime.events == []
    assert event.llm_calls == []
    assert event.stop_calls == 0


@pytest.mark.asyncio
async def test_prior_send_is_not_dispatched_even_if_astrbot_cleared_the_result(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": False})
    event = FakeEvent(
        {"post_type": "message", "message_type": "group", "message": []}
    )
    event._has_send_oper = True
    plugin.runtime = FakeRuntime({"handled": True})

    await plugin.on_onebot_message(event)

    assert plugin.runtime.events == []
    assert event.llm_calls == [False]
    assert event.stop_calls == 0


@pytest.mark.asyncio
async def test_plugin_initialize_and_terminate_own_runtime_lifecycle(monkeypatch, tmp_path):
    plugin_module = _load_plugin(monkeypatch)
    monkeypatch.setattr(sys, "path", list(sys.path))
    state: dict[str, Any] = {}

    class LifecycleRuntime:
        def __init__(self, *, project_root, legacy_chat):
            state["args"] = (project_root, legacy_chat)

        async def initialize(self):
            state["initialized"] = True

        async def close(self):
            state["closed"] = True

    runtime_module = types.ModuleType("app.astrbot_runtime")
    runtime_module.ChatRuntime = LifecycleRuntime
    monkeypatch.setitem(sys.modules, "app.astrbot_runtime", runtime_module)
    monkeypatch.setattr(plugin_module, "_find_project_root", lambda: tmp_path)

    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": False})
    await plugin.initialize()
    assert state["args"] == (tmp_path, False)
    assert state["initialized"] is True

    await plugin.terminate()
    assert state["closed"] is True
    assert plugin.runtime is None


@pytest.mark.asyncio
async def test_failed_initialize_closes_partial_runtime_and_can_retry(monkeypatch, tmp_path):
    plugin_module = _load_plugin(monkeypatch)
    monkeypatch.setattr(sys, "path", list(sys.path))
    state = {"attempts": 0, "closed": 0}

    class RetryRuntime:
        def __init__(self, *, project_root, legacy_chat):
            pass

        async def initialize(self):
            state["attempts"] += 1
            if state["attempts"] == 1:
                raise RuntimeError("temporary initialization failure")

        async def close(self):
            state["closed"] += 1

    runtime_module = types.ModuleType("app.astrbot_runtime")
    runtime_module.ChatRuntime = RetryRuntime
    monkeypatch.setitem(sys.modules, "app.astrbot_runtime", runtime_module)
    monkeypatch.setattr(plugin_module, "_find_project_root", lambda: tmp_path)

    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": True})
    with pytest.raises(RuntimeError, match="temporary initialization failure"):
        await plugin.initialize()
    assert plugin.runtime is None
    assert state["closed"] == 1

    await plugin.initialize()
    assert state["attempts"] == 2
    assert plugin.runtime is not None


def test_message_handler_uses_onebot_filter_and_late_priority(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    filter_calls = plugin_module.QQChatPlugin.on_onebot_message._test_filter_calls

    assert (("group",), {"priority": -100}) in filter_calls
    assert (("aiocqhttp",), {}) in filter_calls
    assert (("all",), {"priority": -100}) in (
        plugin_module.QQChatPlugin.on_wechat_message._test_filter_calls
    )
    assert (("all",), {"priority": -90}) in (
        plugin_module.QQChatPlugin.on_anime_image._test_filter_calls
    )


@pytest.mark.asyncio
async def test_qq_event_is_not_dispatched_by_wechat_handler(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": False})
    runtime = CaptureWechatRuntime()
    plugin.runtime = runtime
    event = FakeWechatEvent(platform_name="aiocqhttp")

    await plugin.on_wechat_message(event)

    assert runtime.events == []
    assert plugin._wechat_bridges == {}
    assert event.llm_calls == []
    assert event.stop_calls == 0


@pytest.mark.asyncio
async def test_wechat_private_and_group_events_get_separate_legacy_routes(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": False})
    runtime = CaptureWechatRuntime()
    plugin.runtime = runtime
    private = FakeWechatEvent()
    group = FakeWechatEvent(
        message_type="GroupMessage",
        session_id="room-9",
        sender_id="peer-2",
        group_id="room-9",
        text="群里好",
    )

    await plugin.on_wechat_message(private)
    await plugin.on_wechat_message(group)

    assert len(runtime.events) == 2
    private_raw, group_raw = runtime.events
    assert private_raw["wechat_private"] is True
    assert private_raw["group_id"].startswith("wechat:group:wx-adapter:wx-account:private:")
    assert private_raw["message"][0]["type"] == "at"
    assert group_raw["wechat_private"] is False
    assert group_raw["group_id"].startswith("wechat:group:wx-adapter:wx-account:group:")
    assert all(segment["type"] != "at" for segment in group_raw["message"])
    assert private_raw["group_id"] != group_raw["group_id"]


@pytest.mark.parametrize(
    "event_kwargs",
    [
        {"result": SimpleNamespace(chain=["已有回复"])},
        {"stopped": True},
        {"sent": True},
    ],
)
@pytest.mark.asyncio
async def test_existing_reply_or_stopped_event_is_not_dispatched_again(monkeypatch, event_kwargs):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": False})
    runtime = CaptureWechatRuntime()
    plugin.runtime = runtime
    event = FakeWechatEvent(**event_kwargs)

    await plugin.on_wechat_message(event)

    assert runtime.events == []
    assert plugin._wechat_bridges == {}
    assert event.stop_calls == 0


@pytest.mark.asyncio
async def test_empty_prior_result_does_not_block_wechat_dispatch(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object(), config={"legacy_chat": False})
    runtime = CaptureWechatRuntime()
    plugin.runtime = runtime
    event = FakeWechatEvent(result=SimpleNamespace(chain=[]))

    await plugin.on_wechat_message(event)

    assert len(runtime.events) == 1
    assert runtime.events[0]["wechat_private"] is True


@pytest.mark.asyncio
async def test_same_account_background_actions_keep_their_original_session(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin_context = CaptureWechatContext()
    plugin = plugin_module.QQChatPlugin(context=plugin_context, config={"legacy_chat": False})
    runtime = CaptureWechatRuntime()
    plugin.runtime = runtime

    class FakeChain:
        def __init__(self, chain):
            self.chain = chain

    class FakePlain:
        def __init__(self, text):
            self.text = text

    class FakeMedia:
        @classmethod
        def fromURL(cls, value):
            return value

        @classmethod
        def fromFileSystem(cls, value):
            return value

        @classmethod
        def fromBase64(cls, value):
            return value

    class FakeFile:
        def __init__(self, name, file="", url=""):
            self.name = name
            self.file = file
            self.url = url

    bridge_module = __import__("app.astrbot_wechat", fromlist=["_astrbot_components"])
    monkeypatch.setattr(
        bridge_module,
        "_astrbot_components",
        lambda: SimpleNamespace(
            MessageChain=FakeChain,
            Plain=FakePlain,
            Image=FakeMedia,
            File=FakeFile,
            Video=FakeMedia,
        ),
    )
    first = FakeWechatEvent(session_id="peer-a", sender_id="peer-a", message_id="a")
    second = FakeWechatEvent(session_id="peer-b", sender_id="peer-b", message_id="b")
    await plugin.on_wechat_message(first)
    await plugin.on_wechat_message(second)

    assert runtime.action_calls[0] is runtime.action_calls[1]
    await runtime.action_calls[0](
        "send_group_msg", group_id=runtime.events[0]["group_id"], message="后台第一段"
    )
    await runtime.action_calls[1](
        "send_group_msg", group_id=runtime.events[1]["group_id"], message="当前会话"
    )
    await runtime.action_calls[0](
        "send_group_msg", group_id=runtime.events[0]["group_id"], message="后台后续段"
    )

    sent = {chain.chain[0].text: umo for umo, chain in plugin_context.sent}
    assert sent["后台第一段"] == first.unified_msg_origin
    assert sent["当前会话"] == second.unified_msg_origin
    assert sent["后台后续段"] == first.unified_msg_origin


@pytest.mark.asyncio
async def test_terminate_closes_runtime_and_clears_wechat_bridges(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object())
    runtime = CaptureWechatRuntime()
    plugin.runtime = runtime
    plugin._wechat_bridges[("wx-adapter", "wx-account")] = object()

    await plugin.terminate()

    assert runtime.closed is True
    assert plugin.runtime is None
    assert plugin._wechat_bridges == {}


@pytest.mark.asyncio
async def test_terminate_clears_bridges_even_if_runtime_close_fails(monkeypatch):
    plugin_module = _load_plugin(monkeypatch)
    plugin = plugin_module.QQChatPlugin(context=object())

    class FailingCloseRuntime(CaptureWechatRuntime):
        async def close(self):
            raise RuntimeError("close failed")

    plugin.runtime = FailingCloseRuntime()
    plugin._wechat_bridges[("wx-adapter", "wx-account")] = object()

    with pytest.raises(RuntimeError, match="close failed"):
        await plugin.terminate()

    assert plugin.runtime is None
    assert plugin._wechat_bridges == {}
