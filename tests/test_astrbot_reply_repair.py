"""直接点名被避重命中时只纠正一次，所有模型均为本地桩。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from test_astrbot_reply_guard import FILLER, TEXT, Event, sent

from app.astrbot_reply_guard import GroupReplyGuard
from app.astrbot_reply_repair import DirectReplyRepair

FIXED = "刚才串到旧话题了，我没接上你这句。你是在吐槽我一直复读，这个批评我认，接下来直接回答当前问题。"


def response(text=FILLER, **kwargs):
    return SimpleNamespace(role="assistant", completion_text=text, result_chain=None,
                           reasoning_content="旧推理", **kwargs)


def direct(prompt="你是豆包扮演的吗", **kwargs):
    event = Event(prompt=prompt, **kwargs)
    event.extras["qqchat_group_reply_reason"] = "direct"
    event.message_obj.raw_message["user_id"] = "849855989"
    event.unified_msg_origin = "qq-local:GroupMessage:1000000001"
    return event


class Provider:
    def __init__(self, text=FIXED, *, error=None, busy=False, delay=0):
        self.text, self.error, self.busy, self.delay = text, error, busy, delay
        self.calls = []

    def qqchat_repair_busy(self):
        return self.busy

    async def qqchat_repair_once(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return response(self.text)


class Context:
    def __init__(self, provider):
        self.provider = provider

    async def get_using_provider_async(self, umo):
        return self.provider


async def test_repair_once_uses_only_current_question_and_recent_human_context():
    guard = GroupReplyGuard()
    provider = Provider()
    helper = DirectReplyRepair(guard)
    event = direct()
    event.extras["qqchat_group_context_snapshot"] = [{"speaker": "群友", "text": "你怎么一直复读"}]
    event.extras["qqchat_group_current_human_text"] = "你是豆包扮演的吗"
    event.prompt = "被其他插件修改后的旧卡组话题"
    original = response()
    assert await helper.repair_response(Context(provider), event, original) == "repaired"
    assert original.completion_text == FIXED and original.reasoning_content is None
    assert len(provider.calls) == 1
    payload = str(provider.calls[0])
    assert "你是豆包扮演的吗" in payload and "你怎么一直复读" in payload
    assert "旧卡组话题" not in payload and FILLER not in payload
    assert "contexts" not in provider.calls[0] and "tools" not in provider.calls[0]
    assert await helper.repair_response(Context(provider), event, original) is None
    assert len(provider.calls) == 1


async def test_duplicate_instead_of_boilerplate_is_repaired():
    guard = GroupReplyGuard()
    sent(guard, Event())
    original = response(TEXT)
    assert await DirectReplyRepair(guard).repair_response(Context(Provider()), direct(), original) == "repaired"
    assert original.completion_text == FIXED


@pytest.mark.parametrize("reason", ["question", "interject", "followup", None])
async def test_automatic_replies_do_not_get_extra_generation_or_notice(reason):
    provider = Provider()
    event = direct()
    event.extras["qqchat_group_reply_reason"] = reason
    original = response()
    assert await DirectReplyRepair(GroupReplyGuard()).repair_response(Context(provider), event, original) is None
    assert original.completion_text == FILLER and not provider.calls


@pytest.mark.parametrize("kind", ["media_in", "media_out", "command", "tool", "chunk", "private"])
async def test_media_commands_and_non_final_responses_are_untouched(kind):
    provider = Provider()
    event, original = direct(), response()
    if kind == "media_in":
        event.message_obj.raw_message["message"].append({"type": "image", "data": {"file": "image"}})
    elif kind == "media_out":
        original.result_chain = SimpleNamespace(chain=[SimpleNamespace(type="Image")])
    elif kind == "command":
        event.extras["qqchat_natural_command"] = "群分析"
    elif kind == "tool":
        original.tools_call_name = ["some_tool"]
    elif kind == "chunk":
        original.is_chunk = True
    elif kind == "private":
        event.message_obj.raw_message["message_type"] = "private"
    assert await DirectReplyRepair(GroupReplyGuard()).repair_response(Context(provider), event, original) is None
    assert original.completion_text == FILLER and not provider.calls


async def test_useful_response_is_not_regenerated():
    provider = Provider()
    original = response(FIXED)
    assert await DirectReplyRepair(GroupReplyGuard()).repair_response(Context(provider), direct(), original) is None
    assert not provider.calls


@pytest.mark.parametrize("provider", [Provider(FILLER), Provider(error=RuntimeError("1302")), None])
async def test_invalid_repair_or_failure_gets_one_honest_notice_and_guard_permits_it(provider):
    guard = GroupReplyGuard()
    original, event = response(), direct()
    result = await DirectReplyRepair(guard).repair_response(Context(provider), event, original)
    assert result == "fallback"
    assert "这次没接上" in original.completion_text
    assert "你是豆包扮演的吗" in original.completion_text
    event.result.chain[0].text = original.completion_text
    assert guard.prepare_result(event) is None
    event._has_send_oper = True
    guard.mark_sent(event)
    # 同样提示即使之前发过，新的事件只有获得精确一次授权才可通过。
    another = direct()
    another.result.chain[0].text = original.completion_text
    assert guard.prepare_result(another) == "duplicate"


async def test_busy_provider_short_circuits_without_cloud_request():
    provider = Provider(busy=True)
    original = response()
    assert await DirectReplyRepair(GroupReplyGuard()).repair_response(Context(provider), direct(), original) == "fallback"
    assert not provider.calls and "这次没接上" in original.completion_text


async def test_twelve_second_bound_can_be_tested_without_waiting_or_retrying():
    provider = Provider(delay=1)
    original = response()
    helper = DirectReplyRepair(GroupReplyGuard(), deadline_seconds=0.01)
    assert await helper.repair_response(Context(provider), direct(), original) == "fallback"
    assert len(provider.calls) == 1 and "这次没接上" in original.completion_text


async def test_same_question_notice_cooldown_but_different_new_question_gets_feedback():
    now = [100.0]
    provider = Provider(error=RuntimeError("busy"))
    guard = GroupReplyGuard()
    helper = DirectReplyRepair(guard, clock=lambda: now[0])
    first = response()
    assert await helper.repair_response(Context(provider), direct(), first) == "fallback"
    repeated = response()
    assert await helper.repair_response(Context(provider), direct(), repeated) == "notice_cooldown"
    assert repeated.completion_text == "" and len(provider.calls) == 1
    different = response()
    assert await helper.repair_response(Context(provider), direct(prompt="你为什么一直重复"), different) == "fallback"
    assert "你为什么一直重复" in different.completion_text and len(provider.calls) == 1
    now[0] += 121
    again, event = response(), direct()
    assert await helper.repair_response(Context(provider), event, again) == "fallback"
    event.result.chain[0].text = again.completion_text
    assert guard.prepare_result(event) is None
    assert len(provider.calls) == 2


async def test_name_addressed_message_also_can_be_repaired():
    event = direct()
    event.extras["qqchat_group_reply_reason"] = "name"
    assert await DirectReplyRepair(GroupReplyGuard()).repair_response(Context(Provider()), event, response()) == "repaired"
