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


@pytest.mark.asyncio
@pytest.mark.parametrize(("question", "expected"), (
    ("帮我对比一下两张图好吗？", "两张"),
    ("一起打雀魂，房号 12345", "房号 12345"),
    ("陪我打炉石传说", "战网"),
    ("大肥鱼，一起玩 Minecraft", "Java"),
    ("这句怎么回", "原话"),
))
async def test_companion_question_serializes_to_onebot_without_mention(monkeypatch, tmp_path, question, expected):
    from unittest.mock import AsyncMock

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    sdk = _load_astrbot_types(root, monkeypatch)
    module = _load_plugin(root, monkeypatch)
    plugin = module.QQChatPlugin(SimpleNamespace(), {
        "legacy_chat": False, "group_chat_groups": ["12345"], "group_chat_addressed_only": True,
    })
    plugin.runtime = SimpleNamespace(_core=None)
    monkeypatch.setattr("app.services.game_clients.CONFIG", tmp_path / "no-game-account.json")
    monkeypatch.setattr(plugin, "_legacy_event_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr("app.astrbot_owner_memory.observe", AsyncMock())
    event, _, raw = _make_group_event(sdk)
    event.message_obj.group_id = str(raw["group_id"])
    event.message_str = question
    event.message_obj.message = [sdk.Plain(question)]
    raw["message"] = [{"type": "text", "data": {"text": question}}]
    outbound = AsyncMock(return_value={"message_id": 75})
    event.bot.send_group_msg = outbound
    assert module._QQChatEarlyFilter().filter(event, {})
    await plugin.route_native_group_message(event)
    assert event.is_stopped()
    outbound.assert_awaited_once()
    segment = outbound.await_args.kwargs["message"][0]
    assert segment["type"] == "text" and expected in segment["data"]["text"]


@pytest.mark.asyncio
async def test_reminder_persists_across_native_scheduler_restart(monkeypatch, tmp_path):
    from datetime import datetime, timedelta

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-scheduler"))
    monkeypatch.chdir(tmp_path)
    sdk = _load_astrbot_types(root, monkeypatch)
    from astrbot.core.cron.manager import CronJobManager
    from astrbot.core.db.sqlite import SQLiteDatabase

    from app import astrbot_chat_actions as actions

    event, _, _ = _make_group_event(sdk)
    event.message_obj.group_id = "12345"
    db = SQLiteDatabase(str(tmp_path / "test.db"))
    await db.initialize()
    manager = CronJobManager(db)
    context = SimpleNamespace(cron_manager=manager)
    plugin = SimpleNamespace(context=context)
    state = SimpleNamespace(last_reminder=None, dialog=actions.Dialog("remind", 100,
        title="测试事项", when=datetime.now(actions.CHINA) + timedelta(days=1)))
    try:
        assert "记好了" in await actions.chat_action(plugin, event, state, "save", "好的", [], 10)
        job, = await manager.list_jobs("active_agent")
        assert job.payload["session"] == event.unified_msg_origin
        assert job.payload["sender_id"] == str(event.get_sender_id())
        await manager.shutdown()
        manager = context.cron_manager = CronJobManager(db)
        await manager.start(context)
        state.last_reminder = None
        assert manager.get_next_run_time(job.job_id)
        assert "收掉" in await actions.chat_action(plugin, event, state, "complete", "我交了", [], 200)
        assert not await manager.list_jobs("active_agent")
    finally:
        await manager.shutdown()
        await db.engine.dispose()


class FakeChatRuntime:
    instances: ClassVar[list[FakeChatRuntime]] = []

    def __init__(self, *, project_root: Path, legacy_chat: bool) -> None:
        self.project_root = project_root
        self.legacy_chat = legacy_chat
        self.events: list[dict[str, Any]] = []
        self.closed = False
        self._core = SimpleNamespace(settings=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(llm=None)))
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
    # New background services are outside the OneBot dispatch contract. Never
    # bind the user's live account/Minecraft ports from this isolated test.
    from test_astrbot_plugin import _isolate_minecraft_bridge
    _isolate_minecraft_bridge(monkeypatch, module)
    class Panel:
        def __init__(self, *args):
            pass

        async def start(self):
            pass

        async def close(self):
            pass
    monkeypatch.setattr("app.services.mahjong.account_panel.AccountPanel", Panel)
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


@pytest.mark.asyncio
async def test_explicitly_enabled_unaddressed_group_chat_enters_native_reply_flow(monkeypatch, tmp_path):
    """Optional old mode requires explicit opt-in; defaults stay addressed-only."""
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-group-participation"))
    root = _find_project_root()
    types = _load_astrbot_types(root, monkeypatch)
    plugin_module = _load_plugin(root, monkeypatch)
    from astrbot.core.message.message_event_result import ResultContentType
    from astrbot.core.provider.entities import ProviderRequest

    from app.astrbot_group_chat import GROUP_PARTICIPATION_DEFAULTS

    plugin = plugin_module.QQChatPlugin(SimpleNamespace(), {
        "legacy_chat": False, "group_chat_groups": ["12345"],
        "group_chat_enabled": True, **GROUP_PARTICIPATION_DEFAULTS,
        "group_chat_addressed_only": False,
    })
    plugin.runtime = SimpleNamespace()

    async def allow(_raw):
        return True

    monkeypatch.setattr(plugin, "_legacy_event_allowed", allow)
    for index in range(6):
        event, api, raw = _make_group_event(types)
        text = "这家面馆的汤还挺香的"
        user_id = str(20000 + index % 2)
        event.message_str = text
        event.message_obj.message = [types.Plain(text)]
        event.message_obj.group_id = "12345"
        event.message_obj.message_id = str(index)
        event.message_obj.sender = types.MessageMember(user_id=user_id, nickname="测试群友")
        raw.update(message_id=index, user_id=int(user_id), message=[{"type": "text", "data": {"text": text}}])
        raw["sender"]["user_id"] = int(user_id)
        assert plugin_module._QQChatEarlyFilter().filter(event, {})
        await plugin.route_native_group_message(event)
        assert not event.is_stopped()
        assert event.is_at_or_wake_command is (index == 5)
        assert api.calls == []
    assert event.get_extra("qqchat_group_reply_reason") == "interject"
    request = ProviderRequest(prompt=text, system_prompt="既有人格")
    plugin._get_group_router().prepare_request(event, request)
    assert "本轮自然参与群聊" in "\n".join(part.text for part in request.extra_user_content_parts)
    result = event.plain_result("汤底好喝的话，本鱼先把碗端稳了")
    result.set_result_content_type(ResultContentType.LLM_RESULT)

    async def send_group_msg(**params):
        return await api.call_action("send_group_msg", **params)

    event.bot.send_group_msg = send_group_msg
    await event.send(result)
    await plugin.finish_native_group_reply(event)
    assert api.calls and api.calls[0][0] == "send_group_msg"
    assert plugin._get_group_router()._state(event).last_reply_at > 0


def test_setting_confirmation_is_plain_and_leaves_failure_details(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-setting-receipt"))
    root = _find_project_root()
    types = _load_astrbot_types(root, monkeypatch)
    from app.astrbot_settings_reply import simplify_qq_setting_result

    event, _api, raw = _make_group_event(types)
    raw["message"] = [{"type": "text", "data": {"text": "设置模板 modern"}}]
    event.set_result(event.chain_result([types.At(qq="12345"), types.Plain("✅ 报告模板已设置为: modern")]))
    assert simplify_qq_setting_result(event)
    assert len(event.get_result().chain) == 1
    assert isinstance(event.get_result().chain[0], types.Plain)
    assert event.get_result().chain[0].text == "好的，我知道了"
    raw["message"][0]["data"]["text"] = "肥鱼，设置模板 modern"
    event.set_extra("qqchat_natural_command", "设置模板 modern")
    event.set_result(event.plain_result("✅ 报告模板已设置为: modern"))
    assert simplify_qq_setting_result(event)
    assert event.get_result().chain[0].text == "好的，我知道了"
    failure = event.plain_result("没有找到这个模板，请重新选择")
    event.set_result(failure)
    assert not simplify_qq_setting_result(event)
    assert event.get_result() is failure
