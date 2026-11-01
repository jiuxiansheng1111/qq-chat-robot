"""菜单只读入口报告当前群实际设置，不触发插件动作或透露其他会话。"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from app.astrbot_plugin_status import (
    companion_photo_requires_private,
    private_companion_photo_hint,
    proactive_group_status,
)


class StatusEvent:
    unified_msg_origin = "qq-local:GroupMessage:1000000002"
    plugins_name = ("*",)

    def __init__(self, text="主动消息"):
        self.text = text
        self.llm_calls = []
        self.stopped = False

    def get_group_id(self):
        return "1000000002"

    def get_message_str(self):
        return self.text

    def should_call_llm(self, value):
        self.llm_calls.append(value)

    def stop_event(self):
        self.stopped = True

    def make_result(self):
        return SimpleNamespace(message=lambda text: text)


def loaded(name, instance, *, activated=True):
    return SimpleNamespace(name=name, star_cls=instance, activated=activated)


def context(*plugins):
    return SimpleNamespace(get_all_stars=lambda: plugins)


@pytest.fixture
def proactive_config():
    return {
        "group_settings": {
            "enable": True, "session_list": [
                "qq-local:GroupMessage:1000000002", "other-adapter:GroupMessage:999666111",
            ], "group_idle_trigger_minutes": 10,
            "schedule_settings": {"min_interval_minutes": 90, "max_interval_minutes": 151,
                                  "quiet_hours": "2-6", "max_unanswered_times": 1},
        },
        "friend_settings": {"enable": True, "session_list": ["private-recipient"], "secret": "private-data"},
    }


def test_proactive_status_reads_current_group_without_mutation_or_other_destinations(proactive_config):
    original = copy.deepcopy(proactive_config)
    instance = SimpleNamespace(config=proactive_config)
    answer = proactive_group_status(context(loaded("astrbot_plugin_proactive_chat", instance)), StatusEvent())
    assert "本群主动消息：开启" in answer
    assert "10分钟" in answer and "90～151分钟" in answer
    assert "免打扰：2-6" in answer and "未回复上限：1次" in answer
    assert "ProactiveChat" in answer
    assert all(secret not in answer for secret in ["999666111", "other-adapter", "private-recipient", "private-data"])
    assert proactive_config == original


def test_proactive_status_uses_read_only_effective_session_override(proactive_config):
    calls = []

    def effective(origin):
        calls.append(origin)
        return {"enable": False, "_from_session_list": True, "group_idle_trigger_minutes": 15,
                "schedule_settings": {"min_interval_minutes": 60, "max_interval_minutes": 120,
                                      "quiet_hours": "0-8", "max_unanswered_times": 2}}

    instance = SimpleNamespace(config=proactive_config, _get_session_config=effective)
    answer = proactive_group_status(context(loaded("astrbot_plugin_proactive_chat", instance)), StatusEvent())
    assert calls == [StatusEvent.unified_msg_origin]
    assert "本群主动消息：关闭" in answer and "15分钟" in answer and "60～120分钟" in answer


@pytest.mark.parametrize("mode", ["global_off", "not_target", "disabled_plugin", "session_excluded"])
def test_proactive_status_does_not_report_unavailable_groups_as_enabled(proactive_config, mode):
    event = StatusEvent()
    plugin = loaded("astrbot_plugin_proactive_chat", SimpleNamespace(config=proactive_config))
    if mode == "global_off":
        proactive_config["group_settings"]["enable"] = False
    elif mode == "not_target":
        proactive_config["group_settings"]["session_list"] = ["other-adapter:GroupMessage:999666111"]
    elif mode == "disabled_plugin":
        plugin.activated = False
    else:
        event.plugins_name = ["some_other_plugin"]
    answer = proactive_group_status(context(plugin), event)
    assert "本群主动消息：开启" not in answer and "999666111" not in answer


def test_private_or_mismatched_origin_cannot_read_group_status(proactive_config):
    event = StatusEvent()
    event.unified_msg_origin = "qq-local:FriendMessage:1000000002"
    ctx = context(loaded("astrbot_plugin_proactive_chat", SimpleNamespace(config=proactive_config)))
    assert "请在要查看的群里" in proactive_group_status(ctx, event)
    event.unified_msg_origin = "qq-local:GroupMessage:999666111"
    assert "请在要查看的群里" in proactive_group_status(ctx, event)


@pytest.mark.parametrize("text", ["陪伴 自拍", "陪伴 生图 一只猫", "/陪伴 画图 天空"])
def test_private_only_photo_entry_gives_usage_without_changing_plugin_scope(text):
    config = {"require_private_opt_in": True}
    plugin = loaded("astrbot_plugin_private_companion", SimpleNamespace(config=config))
    ctx = context(plugin)
    assert companion_photo_requires_private(ctx)
    answer = private_companion_photo_hint(ctx, StatusEvent(text))
    assert "在私聊使用" in answer and "群漫画" in answer
    assert config == {"require_private_opt_in": True}


@pytest.mark.parametrize("text", ["帮我画一张图", "自拍是什么", "不要陪伴 自拍", "群漫画"])
def test_photo_hint_does_not_take_over_other_natural_group_actions(text):
    ctx = context(loaded("astrbot_plugin_private_companion", SimpleNamespace(require_private_opt_in=True)))
    assert private_companion_photo_hint(ctx, StatusEvent(text)) is None


def test_runtime_photo_scope_is_authoritative_and_private_chat_is_unaffected():
    ctx = context(loaded("astrbot_plugin_private_companion", SimpleNamespace(
        require_private_opt_in=False, config={"require_private_opt_in": True},
    )))
    assert not companion_photo_requires_private(ctx)
    assert private_companion_photo_hint(ctx, StatusEvent("陪伴 自拍")) is None


async def test_registered_status_command_ends_without_model_request(monkeypatch, proactive_config):
    from test_astrbot_plugin import _load_plugin

    module = _load_plugin(monkeypatch)
    ctx = context(loaded("astrbot_plugin_proactive_chat", SimpleNamespace(config=proactive_config)))
    plugin = module.QQChatPlugin(ctx, {"legacy_chat": False})
    event = StatusEvent()
    answer = await plugin.proactive_group_settings(event)
    assert "本群主动消息：开启" in answer
    assert event.stopped and event.llm_calls == [False]
    assert plugin.proactive_group_settings._test_filter_calls == [(("主动消息",), {})]


async def test_early_group_photo_hint_stops_before_vendor_private_filter(monkeypatch):
    from test_astrbot_group_chat import Event
    from test_astrbot_plugin import _load_plugin

    module = _load_plugin(monkeypatch)
    ctx = context(loaded("astrbot_plugin_private_companion", SimpleNamespace(require_private_opt_in=True)))
    plugin = module.QQChatPlugin(ctx, {"legacy_chat": False, "group_chat_groups": ["1000000002"]})
    plugin.runtime = object()

    async def allowed(_raw):
        return True

    monkeypatch.setattr(plugin, "_legacy_event_allowed", allowed)
    event = Event("陪伴 自拍", group="1000000002")
    event.get_group_id = lambda: "1000000002"
    event.unified_msg_origin = StatusEvent.unified_msg_origin
    event.make_result = lambda: SimpleNamespace(message=lambda text: text)
    answer = await plugin.route_native_group_message(event)
    assert "在私聊使用" in answer
    assert event.stopped and event.llm_calls == [False]
    assert not plugin._get_group_router()._groups
