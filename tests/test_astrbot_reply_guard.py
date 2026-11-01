"""发送前避重只影响群内普通文字模型回复，不触发真实平台发送。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.astrbot_reply_guard import GroupReplyGuard

TEXT = "我觉得这套卡组的关键是先站住场面，保留解牌处理对手的威胁；如果一直抢脸，后期往往会失去主动。可以先打几局看看自己的节奏。"
FILLER = (
    "哈哈，看来你对我的出现挺惊讶的嘛！作为群聊机器人小丛雨，我在这里就是为了帮助大家解答问题、聊天互动的。"
    "如果你有任何疑问或者需要帮忙，随时可以告诉我哦！"
)


class Result:
    def __init__(self, text, *, llm=True, extra=()):
        self.chain = [SimpleNamespace(type="Plain", text=text), *extra]
        self.llm = llm

    def is_llm_result(self):
        return self.llm


class Event:
    def __init__(self, text=TEXT, *, prompt="你觉得这套卡组怎么样", group="1000000001",
                 self_id="3503565007", platform="qq-local", llm=True, extra=()):
        self.result = Result(text, llm=llm, extra=extra)
        self.extras = {}
        self.stopped = False
        self._has_send_oper = False
        self.platform = platform
        self.message_obj = SimpleNamespace(raw_message={
            "message_type": "group", "group_id": group, "self_id": self_id,
            "message": [{"type": "text", "data": {"text": prompt}}],
        })
        self.prompt = prompt

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_result(self):
        return self.result

    def clear_result(self):
        self.result = None

    def stop_event(self):
        self.stopped = True

    def get_platform_name(self):
        return "aiocqhttp"

    def get_platform_id(self):
        return self.platform

    def get_message_str(self):
        return self.prompt


def sent(guard, event):
    assert guard.prepare_result(event) is None
    event._has_send_oper = True
    guard.mark_sent(event)


@pytest.mark.parametrize("text", [FILLER, "哦，是你啊！有什么可以帮到你的吗？", "有什么我可以帮你的吗？"])
def test_real_repeated_customer_service_filler_is_suppressed_on_first_attempt(text):
    guard = GroupReplyGuard()
    event = Event(text, prompt="憋发癫了")
    assert guard.prepare_result(event) == "boilerplate"
    assert event.result is None and event.stopped


@pytest.mark.parametrize("text", ["你好！", "我建议先关掉自动插话。需要帮忙时再 @ 我。", TEXT])
def test_useful_information_and_normal_greeting_are_retained(text):
    event = Event(text)
    assert GroupReplyGuard().prepare_result(event) is None
    assert event.result is not None and not event.stopped


def test_exact_and_near_duplicate_recent_replies_are_suppressed():
    guard = GroupReplyGuard()
    sent(guard, Event())
    assert guard.prepare_result(Event()) == "duplicate"
    assert guard.prepare_result(Event(TEXT.replace("我觉得", "我的看法是"))) == "duplicate"


@pytest.mark.parametrize("text", ["42", "在的", "是", "不是", "北极星是恒星", "水的沸点是100摄氏度"])
def test_short_facts_and_acknowledgements_can_answer_another_new_request(text):
    guard = GroupReplyGuard()
    sent(guard, Event(text, prompt="第一个问题"))
    event = Event(text, prompt="另一个群友的新问题")
    assert guard.prepare_result(event) is None
    assert event.result is not None and not event.stopped


def test_punctuation_spacing_and_width_do_not_evade_exact_dedup():
    guard = GroupReplyGuard()
    sent(guard, Event("GLM 4：能用，不过资料得核实。"))
    assert guard.prepare_result(Event("ｇｌｍ４ 能用！不过资料得核实")) == "duplicate"


@pytest.mark.parametrize("change", [{"group": "1000000002"}, {"self_id": "other-bot"},
                                    {"platform": "another-platform"}])
def test_group_bot_and_platform_are_isolated(change):
    guard = GroupReplyGuard()
    sent(guard, Event())
    assert guard.prepare_result(Event(**change)) is None


@pytest.mark.parametrize("prompt", ["请再说一遍", "重新发一下", "刚才那段重发", "复述一遍", "原样发给我"])
def test_explicit_repeat_request_bypasses_dedup_and_filler(prompt):
    guard = GroupReplyGuard()
    sent(guard, Event())
    assert guard.prepare_result(Event(prompt=prompt)) is None
    assert guard.prepare_result(Event(FILLER, prompt=prompt)) is None


@pytest.mark.parametrize("prompt", ["不要再说一遍了", "别复述一遍", "为什么一直重发", "重发是什么意思"])
def test_complaint_or_negation_does_not_authorize_repetition(prompt):
    guard = GroupReplyGuard()
    sent(guard, Event())
    assert guard.prepare_result(Event(prompt=prompt)) == "duplicate"


def test_self_introduction_only_on_explicit_request():
    event = Event(FILLER, prompt="请介绍一下你自己")
    assert GroupReplyGuard().prepare_result(event) is None


@pytest.mark.parametrize("component", ["Image", "Record", "Video", "Face", "Node", "File"])
def test_media_results_are_untouched(component):
    guard = GroupReplyGuard()
    sent(guard, Event())
    event = Event(extra=[SimpleNamespace(type=component)])
    original = event.result
    assert guard.prepare_result(event) is None
    assert event.result is original and not event.stopped


@pytest.mark.parametrize("command", ["general", "slash", "natural", "native"])
def test_command_results_are_untouched(command):
    guard = GroupReplyGuard()
    sent(guard, Event())
    event = Event(llm=command != "general", prompt="/群分析" if command == "slash" else "群分析")
    if command == "natural":
        event.extras["qqchat_natural_command"] = "群分析"
    if command == "native":
        event.extras["activated_handlers"] = [SimpleNamespace(
            event_filters=[type("CommandFilter", (), {})()])]
    assert guard.prepare_result(event) is None
    assert event.result is not None


def test_private_chat_is_untouched():
    event = Event(FILLER, group="")
    event.message_obj.raw_message["message_type"] = "private"
    assert GroupReplyGuard().prepare_result(event) is None


def test_pending_concurrent_reply_is_deduplicated_and_cancel_releases_it():
    guard = GroupReplyGuard()
    first = Event()
    assert guard.prepare_result(first) is None
    assert guard.prepare_result(first) is None
    assert guard.prepare_result(Event()) == "duplicate"
    guard.discard(first)
    assert guard.prepare_result(Event()) is None


def test_failed_send_does_not_enter_sent_history():
    guard = GroupReplyGuard()
    first = Event()
    assert guard.prepare_result(first) is None
    guard.mark_sent(first)
    assert guard.prepare_result(Event()) is None


def test_expired_history_and_pending_do_not_suppress():
    now = [1000.0]
    guard = GroupReplyGuard(clock=lambda: now[0], ttl_seconds=60, pending_seconds=5)
    sent(guard, Event())
    now[0] += 61
    assert guard.prepare_result(Event()) is None
    now[0] += 6
    assert guard.prepare_result(Event()) is None


@pytest.mark.parametrize("first,second", [
    (TEXT + "现在胜率是51%。", TEXT + "现在胜率是52%。"),
    (TEXT + "这次适合使用。", TEXT + "这次不适合使用。"),
])
def test_changed_numbers_or_negation_are_not_suppressed_as_near_duplicate(first, second):
    guard = GroupReplyGuard()
    sent(guard, Event(first))
    assert guard.prepare_result(Event(second)) is None


def test_persisted_hash_features_survive_restart_without_saving_messages_or_group_ids(tmp_path):
    path = tmp_path / "nested" / "reply-guard.json"
    guard = GroupReplyGuard(path)
    sent(guard, Event())
    payload = path.read_text(encoding="utf-8")
    assert TEXT not in payload and "1000000001" not in payload and "3503565007" not in payload
    assert GroupReplyGuard(path).prepare_result(Event(TEXT.replace("我觉得", "我的看法是"))) == "duplicate"
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("payload", ["broken", "[]", '{"version":2}',
                                    json.dumps({"version": 1, "entries": [None, {"group": "bad"}]})])
def test_invalid_cache_is_ignored(tmp_path, payload):
    path = tmp_path / "guard.json"
    path.write_text(payload, encoding="utf-8")
    assert GroupReplyGuard(path).prepare_result(Event()) is None


def test_guard_logs_do_not_contain_reply_text(caplog):
    guard = GroupReplyGuard()
    with caplog.at_level("INFO", logger="app.astrbot_reply_guard"):
        guard.prepare_result(Event(FILLER))
    assert "boilerplate" in caplog.text
    assert FILLER not in caplog.text and "有任何疑问" not in caplog.text


def test_real_native_components_and_result_types():
    pytest.importorskip("astrbot")
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType

    def native(event):
        event.result = MessageEventResult().message(TEXT).set_result_content_type(
            ResultContentType.LLM_RESULT)
        return event

    guard = GroupReplyGuard()
    sent(guard, native(Event()))
    duplicate = native(Event())
    duplicate.result.chain.append(At(qq="849855989"))
    assert guard.prepare_result(duplicate) == "duplicate"
    media = native(Event())
    media.result.chain.append(Image.fromURL("https://example.invalid/image.png"))
    assert guard.prepare_result(media) is None
    command = native(Event())
    command.result.set_result_content_type(ResultContentType.GENERAL_RESULT)
    assert guard.prepare_result(command) is None
    assert isinstance(command.result.chain[0], Plain)


async def test_real_native_pre_send_hook_clears_duplicate_before_respond(monkeypatch):
    pytest.importorskip("astrbot")
    from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
    from astrbot.core.pipeline.respond.stage import RespondStage
    from astrbot.core.pipeline.result_decorate import stage as decorating

    guard = GroupReplyGuard()
    sent(guard, Event())
    event = Event()
    event.result = MessageEventResult().message(TEXT).set_result_content_type(
        ResultContentType.LLM_RESULT)
    event.is_stopped = lambda: event.stopped

    async def forbidden_send(*args, **kwargs):
        pytest.fail("重复文字不得进入真实发送适配器")

    async def handler(current):
        assert guard.prepare_result(current) == "duplicate"

    event.send = forbidden_send
    metadata = SimpleNamespace(handler=handler, handler_module_path="test.guard",
                               handler_name="guard")
    monkeypatch.setattr(decorating.star_handlers_registry, "get_handlers_by_event_type",
                        lambda *args, **kwargs: [metadata])
    monkeypatch.setitem(decorating.star_map, "test.guard", SimpleNamespace(name="test.guard"))
    event.plugins_name = None
    stage = decorating.ResultDecorateStage()
    stage.content_safe_check_reply = False
    async for _ in stage.process(event):
        pass
    await RespondStage().process(event)
    assert event.result is None and event.stopped and not event._has_send_oper
