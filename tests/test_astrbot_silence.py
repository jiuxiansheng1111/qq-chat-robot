from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_astrbot_reply_guard import Event
from test_astrbot_reply_repair import Context, DirectReplyRepair, Provider, direct, response

from app.astrbot_group_chat import GroupChatRouter
from app.astrbot_reply_guard import GroupReplyGuard
from app.astrbot_silence import NO_REPLY_MARKER, is_silence_control_text

VARIANTS = ("保持安静", "保持安静。", "[保持安静]", "（保持安静。）", "**保持安静。**",
            "“保持安静”", "\u200b保持安静。\n", "```text\n保持安静。\n```",
            NO_REPLY_MARKER, "[QQCHAT_NO_REPLY]。", "ＱＱＣＨＡＴ＿ＮＯ＿ＲＥＰＬＹ")


@pytest.mark.parametrize("text", VARIANTS)
def test_standalone_control_variants_are_suppressed_before_send(text):
    event = Event(text)
    event.extras["qqchat_group_reply_reason"] = "interject"
    assert is_silence_control_text(text)
    assert GroupReplyGuard().prepare_result(event) == "silence_control"
    assert event.result is None and event.stopped and not event._has_send_oper


@pytest.mark.parametrize("text", ["图书馆里要保持安静，别打扰别人。", "为什么你会说保持安静？",
                                  "这首歌叫《保持安静》。", "我会先不插话，你需要时再叫我。"])
def test_substantive_answers_are_not_removed_just_for_mentioning_silence(text):
    assert not is_silence_control_text(text)
    event = Event(text)
    assert GroupReplyGuard().prepare_result(event) is None and event.result is not None


@pytest.mark.parametrize("prompt", ["把keep quiet翻译成中文", "只回答四个字：保持安静", "请原样输出‘保持安静’"])
def test_explicit_literal_or_translation_requests_are_preserved(prompt):
    event = Event("保持安静。", prompt=prompt)
    event.extras["qqchat_group_reply_reason"] = "direct"
    assert GroupReplyGuard().prepare_result(event) is None
    assert event.result is not None


def test_complaints_about_the_marker_are_not_literal_output_requests():
    event = Event("保持安静。", prompt="为什么你总是输出保持安静")
    assert GroupReplyGuard().prepare_result(event) == "silence_control"


def test_images_and_non_llm_command_results_are_preserved():
    for event in (Event("保持安静。", llm=False),
                  Event("保持安静。", extra=[SimpleNamespace(type="Image")])):
        assert GroupReplyGuard().prepare_result(event) is None and event.result is not None


async def test_direct_question_gets_one_clean_repair_instead_of_a_silent_failure():
    guard = GroupReplyGuard()
    provider = Provider(text="在呢，你刚才问的是什么情况呀？")
    event, original = direct(prompt="在不在呀"), response("保持安静。")
    assert await DirectReplyRepair(guard).repair_response(Context(provider), event, original) == "repaired"
    assert len(provider.calls) == 1 and original.completion_text == provider.text
    assert not is_silence_control_text(original.completion_text)


async def test_real_proactive_sender_stops_without_sending_or_recording_a_silence_word(monkeypatch, tmp_path):
    pytest.importorskip("astrbot")
    import astrbot.api  # noqa: F401 -- 初始化原生事件依赖。
    from astrbot.core.message.components import Plain
    from astrbot.core.platform.message_type import MessageType

    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "astrbot"))
    root = Path(__file__).resolve().parents[1]
    package = types.ModuleType("_silence_proactive")
    source = root / "data/astrbot/instance/data/plugins/astrbot_plugin_proactive_chat"
    if not source.is_dir():
        pytest.skip("需要已安装的主动聊天插件以验证实际发送器")
    package.__path__ = [str(source)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    events = importlib.import_module(package.__name__ + ".core.proactive_event")
    sender_module = importlib.import_module(package.__name__ + ".core.message_sender")
    router = GroupChatRouter({"group_chat_groups": ["1000000001"]})
    guard = GroupReplyGuard()

    async def decorate(event, event_type):
        assert router.is_idle_event(event)
        assert guard.prepare_result(event) == "silence_control"
        router.suppress_reply(event)
        return event.is_stopped()

    monkeypatch.setattr(sender_module, "dispatch_event_hook", decorate)
    for text in ("保持安静。", NO_REPLY_MARKER):
        event = events.ProactiveMessageEvent(
            plugin=SimpleNamespace(), platform_meta=SimpleNamespace(name="aiocqhttp", id="qq-local"),
            session_id="qq-local:GroupMessage:1000000001", target_id="1000000001",
            message_type=MessageType.GROUP_MESSAGE, self_id="999990001", is_group=True,
        )
        sender = sender_module.SenderMixin()
        chain, should_send = await sender._run_decorating_hooks(event, [Plain(text)])
        assert chain == [] and not should_send and event.is_stopped()
        assert not event.proactive_sent_chains and not event._has_send_oper
