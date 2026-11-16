"""普通聊天仅在多人短时间热聊时参与，明确点名与冷场出口独立。"""

from types import SimpleNamespace

from test_astrbot_group_chat import Event

from app.astrbot_group_chat import GroupChatRouter


def active_config():
    return {
        "group_chat_enabled": True, "group_chat_groups": ["1000000001", "1000000002"],
        "group_chat_bot_names": ["小丛雨", "丛雨"],
        "group_chat_addressed_only": False, "group_chat_require_engagement": True,
        "group_chat_interject_every": 12, "group_chat_active_speakers": 3,
        "group_chat_active_window_seconds": 300, "group_chat_min_interval_seconds": 600,
    }


def test_ordinary_questions_do_not_wake_the_bot():
    router = GroupChatRouter(active_config())
    for index, text in enumerate(("你改120fov了吗", "这是什么？", "怎么配队", "原神牛逼吗")):
        event = Event(text, user=str(100 + index), message_id=str(index))
        assert router.observe_event(event) is None
        assert event.llm_calls == [False] and not event.is_at_or_wake_command


def test_one_speaker_many_messages_or_slow_conversation_is_not_hot():
    now = [1000.0]
    router = GroupChatRouter(active_config(), clock=lambda: now[0])
    for index in range(20):
        assert router.observe_event(Event(message_id=f"single-{index}")) is None
    for index in range(30):
        now[0] += 120
        assert router.observe_event(Event(user=str(100 + index % 3), message_id=f"slow-{index}")) is None


def test_twelve_messages_three_speakers_interject_once_then_wait_for_engagement():
    now = [1000.0]
    router = GroupChatRouter(active_config(), clock=lambda: now[0])
    for index in range(12):
        now[0] += 5
        event = Event(user=str(100 + index % 3), message_id=str(index))
        assert router.observe_event(event) == ("interject" if index == 11 else None)
    router.finish_event(event, sent=True)
    now[0] += 601
    for index in range(12, 40):
        now[0] += 5
        assert router.observe_event(Event(user=str(100 + index % 3), message_id=str(index))) is None
    direct = Event("你是豆包扮演的吗", message_id="direct", segments=[
        {"type": "at", "data": {"qq": "999990001"}},
        {"type": "text", "data": {"text": "你是豆包扮演的吗"}},
    ])
    assert router.observe_event(direct) == "direct"


def idle_event(group="1000000001"):
    event = Event(group=group)
    event.message_obj.raw_message = None
    event.message_obj.message_id = "proactive:test"
    event.get_group_id = lambda: group
    event.get_self_id = lambda: "999990001"
    return event


def test_idle_opening_uses_same_group_unanswered_and_quiet_state():
    router = GroupChatRouter(active_config())
    idle = idle_event()
    assert router.is_idle_event(idle) and router.allow_reply(idle)
    router.finish_event(idle, sent=True)
    assert not router.allow_reply(idle_event())
    assert router.allow_reply(idle_event("1000000002"))
    direct = Event("小丛雨，帮我看看", message_id="new")
    assert router.observe_event(direct) == "name"
    router.finish_event(direct, sent=True)
    assert router.allow_reply(idle_event())
    quiet = Event("小丛雨，闭嘴", message_id="quiet")
    router.observe_event(quiet)
    assert not router.allow_reply(idle_event())


def test_hot_chat_prompt_does_not_order_the_bot_to_answer_another_persons_question():
    router = GroupChatRouter(active_config(), clock=lambda: 1000)
    for index in range(12):
        event = Event("你改120fov了吗", user=str(100 + index % 3), message_id=str(index))
        reason = router.observe_event(event)
    assert reason == "interject"
    request = SimpleNamespace(system_prompt="", extra_user_content_parts=[])
    router.prepare_request(event, request)
    text = request.system_prompt + "".join(getattr(part, "text", "") for part in request.extra_user_content_parts)
    assert "分清群友在对谁说话" in text and "[QQCHAT_NO_REPLY]" in text
    assert "【现在回答】" not in text
