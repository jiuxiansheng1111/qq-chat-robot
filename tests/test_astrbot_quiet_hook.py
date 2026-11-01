"""发送前拦截必须早于语音装饰，且释放对应群的自动接话占位。"""

from types import SimpleNamespace

import pytest
from test_astrbot_plugin import FakeEvent, _load_plugin


async def test_repeated_chat_is_suppressed_before_voice_and_closes_passive_budget(monkeypatch):
    module = _load_plugin(monkeypatch)
    calls = []
    plugin = module.QQChatPlugin(object(), {"legacy_chat": False})
    plugin.runtime = object()
    plugin._group_router = SimpleNamespace(
        in_scope=lambda event: True, allow_reply=lambda event: True,
        suppress_reply=lambda event: calls.append("close-budget"),
    )
    event = FakeEvent({}, result=SimpleNamespace(is_llm_result=lambda: True, get_plain_text=lambda: "old"))

    def reject(event):
        calls.append("guard")
        event.clear_result()
        event.stop_event()
        return "duplicate"

    plugin._reply_guard = SimpleNamespace(prepare_result=reject)
    await plugin.suppress_repeated_group_reply(event)
    assert calls == ["guard", "close-budget"]
    assert event.is_stopped() and event.get_result() is None
    assert (plugin.suppress_repeated_group_reply._test_filter_calls[0][1]["priority"]
            > plugin.filter_qq_source_links._test_filter_calls[0][1]["priority"])


async def test_quiet_request_prevents_a_previously_queued_llm_result(monkeypatch):
    module = _load_plugin(monkeypatch)
    calls = []
    plugin = module.QQChatPlugin(object(), {"legacy_chat": False})
    plugin.runtime = object()
    plugin._group_router = SimpleNamespace(
        in_scope=lambda event: True, allow_reply=lambda event: False,
        finish_event=lambda event: calls.append("released"),
    )
    plugin._reply_guard = SimpleNamespace(discard=lambda event: calls.append("discarded"))
    event = FakeEvent({}, result=SimpleNamespace(is_llm_result=lambda: True, get_plain_text=lambda: "old"))
    await plugin.suppress_repeated_group_reply(event)
    assert calls == ["discarded", "released"]
    assert event.get_result() is None and event.is_stopped()


async def test_command_results_skip_chat_deduplication(monkeypatch):
    module = _load_plugin(monkeypatch)
    plugin = module.QQChatPlugin(object(), {"legacy_chat": False})
    plugin.runtime = object()
    plugin._group_router = SimpleNamespace(in_scope=lambda event: True)
    result = SimpleNamespace(is_llm_result=lambda: False)
    event = FakeEvent({}, result=result)
    await plugin.suppress_repeated_group_reply(event)
    assert event.get_result() is result and not event.is_stopped()
    assert plugin._reply_guard is None


async def test_direct_response_hook_repairs_before_decorating_and_releases_muted_notice(monkeypatch):
    module = _load_plugin(monkeypatch)
    calls = []
    plugin = module.QQChatPlugin(object(), {"legacy_chat": False})
    plugin.runtime = object()
    plugin._group_router = SimpleNamespace(
        in_scope=lambda event: True, allow_reply=lambda event: True,
        finish_event=lambda event: calls.append("released"),
    )
    event = FakeEvent({})
    event.get_extra = lambda key, default=None: "direct" if key == "qqchat_group_reply_reason" else default

    async def repair(context, current, response):
        assert current is event
        calls.append("repair")
        return "notice_cooldown"

    plugin._reply_repair = SimpleNamespace(repair_response=repair)
    await plugin.repair_direct_group_reply(event, SimpleNamespace(completion_text="old"))
    assert calls == ["repair", "released"]
    assert event.is_stopped() and event.llm_calls == [False]


@pytest.mark.parametrize("control", ["[保持安静]", "保持安静。", "（保持安静）", "[QQCHAT_NO_REPLY]。"])
async def test_hot_chat_can_choose_silence_without_sending_a_sentinel(monkeypatch, control):
    module = _load_plugin(monkeypatch)
    calls = []
    plugin = module.QQChatPlugin(object(), {"legacy_chat": False})
    plugin.runtime = object()
    plugin._group_router = SimpleNamespace(
        in_scope=lambda event: True, allow_reply=lambda event: True,
        suppress_reply=lambda event: calls.append("quiet"),
    )
    event = FakeEvent({})
    event.get_extra = lambda key, default=None: "interject" if key == "qqchat_group_reply_reason" else default
    response = SimpleNamespace(completion_text=control, result_chain=SimpleNamespace(chain=["marker"]))
    await plugin.repair_direct_group_reply(event, response)
    assert calls == ["quiet"] and event.is_stopped()
    assert response.completion_text == "" and response.result_chain.chain == []
    assert plugin._reply_repair is None
