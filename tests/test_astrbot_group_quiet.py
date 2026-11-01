"""回放无人接话、频繁群问句和让机器人安静的场景。"""

from test_astrbot_group_chat import Event

from app.astrbot_group_chat import GroupChatRouter


def quiet_config():
    return {
        "group_chat_enabled": True, "group_chat_groups": ["1000000001", "1000000002"],
        "group_chat_bot_names": ["小丛雨", "丛雨"],
        "group_chat_unaddressed_questions": True, "group_chat_active_window_seconds": 0,
        "group_chat_min_interval_seconds": 300,
    }


def test_unanswered_passive_reply_does_not_rearm_on_unrelated_human_activity():
    now = [1000.0]
    router = GroupChatRouter(quiet_config(), clock=lambda: now[0])
    question = Event("这个角色怎么配队？")
    assert router.observe_event(question) == "question"
    router.finish_event(question, sent=True)
    for index in range(2, 40):
        now[0] += 600
        event = Event("大家今天玩什么？", user="200", message_id=str(index))
        assert router.observe_event(event) is None
        assert event.llm_calls == [False]
    addressed = Event("小丛雨，帮我看看这个问题", message_id="addressed")
    assert router.observe_event(addressed) == "name"
    router.finish_event(addressed, sent=True)
    now[0] += 301
    fresh = Event("这件事该怎么处理？", message_id="fresh")
    assert router.observe_event(fresh) == "question"


def test_passive_questions_and_interjections_share_cooldown_but_at_is_timely():
    now = [1000.0]
    router = GroupChatRouter(quiet_config(), clock=lambda: now[0])
    direct = Event("小丛雨，你觉得怎么样？")
    router.observe_event(direct)
    router.finish_event(direct, sent=True)
    for index in range(2, 22):
        now[0] += 1
        assert router.observe_event(Event(message_id=str(index))) is None
    assert router.observe_event(Event("怎么了？", message_id="question")) is None
    direct2 = Event("这个怎么算？", message_id="direct2", segments=[
        {"type": "at", "data": {"qq": "3503565007"}},
        {"type": "text", "data": {"text": "这个怎么算？"}},
    ])
    assert router.observe_event(direct2) == "direct"
    router.finish_event(direct2, sent=True)
    now[0] += 300
    for index in range(11):
        assert router.observe_event(Event(message_id=f"after-{index}")) is None
    assert router.observe_event(Event(message_id="after-cooldown")) == "interject"


def test_same_speaker_new_chatter_is_not_automatically_a_followup():
    router = GroupChatRouter(quiet_config(), clock=lambda: 1000)
    direct = Event("小丛雨，今天聊到这里")
    router.observe_event(direct)
    router.finish_event(direct, sent=True)
    assert router.observe_event(Event("我去吃饭了", message_id="2")) is None


def test_reply_to_another_group_member_is_not_a_bot_question():
    Reply = type("Reply", (), {})
    reply = Reply()
    reply.sender_id = "200"
    router = GroupChatRouter(quiet_config())
    event = Event("你刚才什么意思？", components=[reply])
    assert router.observe_event(event) is None
    assert not event.is_at_or_wake_command


def test_silence_request_cancels_pending_chat_without_an_apology_message():
    now = [1000.0]
    router = GroupChatRouter(quiet_config(), clock=lambda: now[0])
    pending = Event("今天玩什么？")
    assert router.observe_event(pending) == "question"
    for index, phrase in enumerate(("憋发癫了", "别复读了", "闭嘴", "不要刷屏了")):
        event = Event(phrase, message_id=f"quiet-{index}", segments=[
            {"type": "at", "data": {"qq": "3503565007"}},
            {"type": "text", "data": {"text": phrase}},
        ])
        assert router.observe_event(event) is None
        assert event.stopped and event.llm_calls == [False]
        assert not router.allow_reply(pending)
    router.finish_event(pending)
    now[0] += 301
    assert router.observe_event(Event("怎么配队？", message_id="passive")) is None
    new_direct = Event("小丛雨，重新问你个问题", message_id="new-direct")
    assert router.observe_event(new_direct) == "name"
    assert router.allow_reply(new_direct)


def test_silencing_one_group_does_not_mute_the_other_group_or_run_commands():
    router = GroupChatRouter(quiet_config())
    quiet = Event("小丛雨，安静一会", message_id="quiet")
    assert router.observe_event(quiet) is None and quiet.stopped
    other = Event("今天怎么配队？", group="1000000002", message_id="other")
    assert router.observe_event(other) == "question"
    command = Event("群分析", message_id="command")
    router.prepare_event(command)
    assert router.observe_event(command) == "command"
    assert not command.stopped
