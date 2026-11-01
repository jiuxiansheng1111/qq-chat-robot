"""自然命令和人类消息接话共用原生流程，不产生独立发送任务。"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from app.astrbot_group_chat import (
    GroupChatRouter,
    normalize_natural_command,
    normalized_runtime_event,
)


class Event:
    def __init__(
        self, text="今天吃了拉面", *, group="1000000001", user="100", self_id="3503565007",
        message_id="1", platform_id="qq-local", platform="aiocqhttp", segments=None,
        components=None, role="member", bot=False,
    ):
        self.message_str = text
        self.extras = {}
        self.platform = platform
        self.platform_id = platform_id
        self.is_at_or_wake_command = False
        self.llm_calls = []
        self.stopped = False
        self.message_obj = SimpleNamespace(
            raw_message={
                "post_type": "message", "message_type": "group", "group_id": group,
                "user_id": user, "self_id": self_id, "message_id": message_id,
                "sender": {"user_id": user, "nickname": "群友", "role": role, "is_bot": bot},
                "message": segments if segments is not None else [{
                    "type": "text", "data": {"text": text},
                }],
            },
            message=components or [],
        )

    def get_message_str(self):
        return self.message_str

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_platform_name(self):
        return self.platform

    def get_platform_id(self):
        return self.platform_id

    def should_call_llm(self, value):
        self.llm_calls.append(value)

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped


@pytest.fixture
def config():
    return {
        "legacy_chat": False, "group_chat_enabled": True, "natural_commands_enabled": True,
        "group_chat_groups": ["1000000001", "1000000002"],
        "group_chat_bot_names": ["小丛雨", "丛雨"], "group_chat_interject_every": 3,
        "group_chat_min_interval_seconds": 0, "group_chat_require_engagement": False,
        "group_chat_followup_seconds": 180,
        "group_chat_unaddressed_questions": True, "group_chat_active_window_seconds": 0,
    }


@pytest.mark.parametrize(("text", "expected"), [
    ("群分析", "群分析"),
    ("群分析 3", "群分析 3"),
    ("群分析一下", "群分析"),
    ("小丛雨，帮我分析一下最近3天的群聊", "群分析 3"),
    ("请帮我总结一下本群", "群分析"),
    ("帮我生成一份群分析报告吧", "群分析"),
    ("群漫画", "群漫画"),
    ("能不能帮我生成一张群漫画？", "群漫画"),
    ("生成一张群漫画可以吗", "群漫画"),
    ("画一下最近2天的群聊漫画", "群漫画 2"),
    ("陪伴群 状态", "陪伴群 状态"),
    ("陪伴群状态", "陪伴群 状态"),
    ("给我看看群陪伴的状态", "陪伴群 状态"),
    ("群陪伴现在什么状态", "陪伴群 状态"),
    ("陪伴群 关闭", "陪伴群 关闭"),
    ("learning_status", "learning_status"),
    ("start_learning", "start_learning"),
    ("表情管理 查看图库", "表情管理 查看图库"),
    ("执行 设置模板 modern", "设置模板 modern"),
    ("牌谱分析", "牌谱分析"),
    ("帮我分析一下这个对局", "牌谱分析"),
    ("牌谱分析 座位=东 赛制=半庄 文件=finished.json", "牌谱分析 座位=东 赛制=半庄 文件=finished.json"),
])
def test_natural_commands_are_full_requests(text, expected):
    assert normalize_natural_command(
        text, names=["小丛雨", "丛雨"],
        commands=["start_learning", "表情管理 查看图库", "设置模板"],
    ) == expected


@pytest.mark.parametrize("text", [
    "不要群分析", "不用帮我分析群聊", "别生成群漫画", "先不陪伴群 状态",
    "群分析怎么用", "群分析是不是很慢", "群分析 之前报错了", "群漫画太慢了",
    "群漫画好不好", "群分析可以吗",
    "我想了解群分析这个功能", "我刚才说的是群分析", "他说群分析很好玩",
    "`群分析`", "“群漫画”", "> 陪伴群 状态", "设置模板 modern", "关闭群陪伴吧",
    "不要执行 start_learning", "别帮我分析牌谱", "牌谱分析 怎么用", "牌谱分析 好不好", "/群分析 3",
])
def test_negated_quoted_and_discussed_commands_do_not_execute(text):
    assert normalize_natural_command(
        text, commands=["start_learning", "设置模板", "陪伴群"],
    ) is None


@pytest.mark.parametrize(("text", "target"), [
    ("群报设置", "分析设置"), ("群分析设置", "分析设置"), ("日报设置", "分析设置"),
    ("设置模板查看使用指南", "查看模板"), ("设置模板帮助", "查看模板"), ("模板列表", "查看模板"),
    ("学习设置", "learning_status"), ("学习状态", "learning_status"),
])
def test_read_only_menu_labels_map_only_to_registered_commands(text, target):
    assert normalize_natural_command(text, commands=[target]) == target
    assert normalize_natural_command(text, commands=[]) is None
    assert normalize_natural_command("不要" + text, commands=[target]) is None
    assert normalize_natural_command(text + "是什么功能", commands=[target]) is None
    assert normalize_natural_command("“" + text + "”", commands=[target]) is None


@pytest.mark.parametrize("text", [
    "群报", "看看群报", "请发一下群报", "帮我生成本群群报", "最新群报", "昨天和今天的群报",
    "群总结", "群日报", "补发群总结", "请补发一下群日报", "帮我重发群报", "最新群总结",
])
def test_calendar_reports_have_a_separate_member_command(text):
    assert normalize_natural_command(text, commands=["群报"]) == "群报"
    assert normalize_natural_command("不要" + text, commands=["群报"]) is None
    assert normalize_natural_command(text + "怎么用", commands=["群报"]) is None
    assert normalize_natural_command("“" + text + "”", commands=["群报"]) is None
    assert normalize_natural_command(text, commands=[]) is None


@pytest.mark.parametrize(("text", "date_text"), [
    ("补发昨天的群总结", "昨天"), ("请补发昨日群日报", "昨日"),
    ("重发今天群报", "今天"), ("2026-10-27的群总结", "2026-10-27"),
    ("群报 2026-10-27", "2026-10-27"), ("群总结 前天", "前天"),
])
def test_single_day_report_requests_preserve_date(text, date_text):
    assert normalize_natural_command(text, commands=["群报"]) == "群报 " + date_text
    for value in ("不要" + text, "“" + text + "”", text + "怎么用"):
        assert normalize_natural_command(value, commands=["群报"]) is None
    assert normalize_natural_command(text, commands=[]) is None


def test_report_command_bypasses_passive_chat_cooldown(config):
    config.update(group_chat_min_interval_seconds=600, group_chat_require_engagement=True)
    router = GroupChatRouter(config, clock=lambda: 1000.0)
    request = Event("补发昨天的群总结")
    state = router._state(request)
    state.last_reply_at = 999.0
    state.passive_unanswered = True
    assert router.prepare_event(request, commands=["群报"])
    assert request.message_str == "群报 昨天"
    assert router.observe_event(request) == "command"


def test_early_normalization_is_idempotent_and_preserves_original_media(config):
    event = Event("能不能帮我分析一下这个对局", segments=[
        {"type": "reply", "data": {"id": "77"}},
        {"type": "file", "data": {"name": "finished.json", "file_id": "opaque"}},
        {"type": "text", "data": {"text": "能不能帮我分析一下这个对局"}},
    ])
    # Existing replay intent deliberately accepts its documented natural phrase.
    event.message_str = "帮我分析一下这个对局"
    before = copy.deepcopy(event.message_obj.raw_message)
    router = GroupChatRouter(config)
    assert router.prepare_event(event)
    assert event.is_at_or_wake_command
    assert event.message_str == "牌谱分析"
    assert event.get_extra("qqchat_original_message_str") == "帮我分析一下这个对局"
    assert router.prepare_event(event)
    normalized = normalized_runtime_event(event, event.message_obj.raw_message)
    assert event.message_obj.raw_message == before
    assert normalized["message"][:2] == before["message"][:2]
    assert normalized["message"][-1]["data"]["text"] == "/牌谱分析"


def test_regular_interjections_count_only_new_human_text_and_never_tick(config):
    now = [1000.0]
    router = GroupChatRouter(config, clock=lambda: now[0])
    assert router.observe_event(Event(message_id="1")) is None
    assert router.observe_event(Event(message_id="2")) is None
    third = Event(message_id="3")
    assert router.observe_event(third) == "interject"
    assert third.is_at_or_wake_command
    # No timer or task exists; even a long idle period cannot create another turn.
    now[0] += 86400
    assert router.observe_event(third) is None
    assert len(router._state(third).seen) == 3
    own = Event("我再补充一句", user="3503565007", message_id="4")
    assert router.prepare_event(own)
    assert router.observe_event(own) is None
    assert not own.is_at_or_wake_command
    assert own.llm_calls == [False]
    assert len(router._state(third).seen) == 3


def test_pending_interjection_does_not_queue_passive_replies_but_direct_address_passes(config):
    router = GroupChatRouter(config, clock=lambda: 1000)
    for i in range(3):
        event = Event(message_id=str(i))
        reason = router.observe_event(event)
    assert reason == "interject"
    for i in range(3, 10):
        assert router.observe_event(Event(message_id=str(i))) is None
    question = Event("玩这个应该怎么配队", user="200", message_id="10")
    assert router.observe_event(question) is None
    question = Event("小丛雨，玩这个应该怎么配队", user="200", message_id="11")
    assert router.observe_event(question) == "name"
    router.finish_event(question, sent=True)
    assert router._state(question).human_messages == 0


@pytest.mark.parametrize("kind", ["at", "reply", "name", "question", "native_wake"])
def test_direct_address_and_questions_reply_without_count_cooldown(config, kind):
    event = Event("今天玩什么", message_id="1")
    if kind == "at":
        event.message_str = "你好"
        event.message_obj.raw_message["message"] = [{"type": "at", "data": {"qq": "3503565007"}}]
    elif kind == "reply":
        Reply = type("Reply", (), {})
        reply = Reply()
        reply.sender_id = "3503565007"
        event.message_obj.message = [reply]
        event.message_str = "继续说说"
    elif kind == "name":
        event.message_str = "小丛雨过来聊聊"
    elif kind == "native_wake":
        event.is_at_or_wake_command = True
        event.message_str = "你好"
    router = GroupChatRouter(config)
    assert router.observe_event(event) in {"direct", "name", "question"}
    assert event.is_at_or_wake_command


def test_other_people_conversation_and_pure_images_do_not_randomly_wake(config):
    router = GroupChatRouter(config)
    for index in range(6):
        assert router.observe_event(Event("", message_id=str(index), segments=[{
            "type": "image", "data": {"file": "unknown.jpg"},
        }])) is None
    other = Event("你觉得怎么样？", message_id="7", segments=[{
        "type": "at", "data": {"qq": "200"},
    }])
    assert router.observe_event(other) is None
    assert router._state(other).human_messages == 0
    explicit = Event("这是什么？", message_id="8", segments=[{
        "type": "image", "data": {"file": "unknown.jpg"},
    }])
    assert router.observe_event(explicit) == "question"


@pytest.mark.parametrize(("field", "value"), [
    ("group", "1000000002"), ("self_id", "888"), ("platform_id", "qq-second"),
])
def test_counters_context_and_message_ids_are_isolated(config, field, value):
    router = GroupChatRouter(config)
    for i in range(2):
        assert router.observe_event(Event("A群刚聊的事情", message_id=str(i))) is None
    separated = Event("B群另一个话题", message_id="0", **{field: value})
    assert router.observe_event(separated) is None
    assert router._state(separated).human_messages == 1
    original = Event("A群第三句", message_id="3")
    assert router.observe_event(original) == "interject"
    request = SimpleNamespace(system_prompt="已有角色上下文")
    router.prepare_request(original, request)
    assert "A群刚聊的事情" in request.system_prompt
    assert "B群另一个话题" not in request.system_prompt
    assert "只针对最新这条人类消息发一条" in request.system_prompt
    before = request.system_prompt
    router.prepare_request(original, request)
    assert request.system_prompt == before


def test_redelivered_message_cannot_reply_twice(config):
    router = GroupChatRouter(config)
    assert router.observe_event(Event("怎么配队？", message_id="same")) == "question"
    duplicate = Event("怎么配队？", message_id="same")
    duplicate.is_at_or_wake_command = True
    assert router.observe_event(duplicate) is None
    assert duplicate.stopped and duplicate.llm_calls == [False]
    assert not duplicate.is_at_or_wake_command


def test_delayed_request_uses_context_snapshot_not_later_unrelated_topic(config):
    router = GroupChatRouter(config)
    first = Event("这个角色怎么样？", message_id="1")
    assert router.observe_event(first) == "question"
    router.observe_event(Event("后来的另一个无关话题", user="200", message_id="2"))
    request = SimpleNamespace(system_prompt="角色")
    router.prepare_request(first, request)
    assert "这个角色怎么样" in request.system_prompt
    assert "后来的另一个无关话题" not in request.system_prompt


@pytest.fixture
def diversity_history():
    # SelfLearning实际生成的跨插件格式：只针对这套完整双边界模板去掉旧Bot示例。
    return (
        "【你最近的回复历史】\n参考这些回复，避免使用相同的开场白、结尾、句式和表达方式：\n"
        "1. 上一轮错误示例：寒冰屏障在标准模式可以用。\n"
        "2. 另一条旧Bot回答。\n"
        "\n特别注意：\n"
        '- 避免重复使用上述回复中的开场白（如"好的"、"明白了"等）\n'
        "- 避免重复使用上述回复中的结尾语\n"
        "- 尝试使用与上述回复不同的句式结构\n"
        "- 尝试使用不同的表达逻辑结构，允许语言中出现口语化、非标准表达\n"
        "- 如果观点相似，也要用不同的表达方式，建议用一定的合理的倒装句、省略句等"
    )


def _all_request_text(request):
    return request.system_prompt + "\n" + "\n".join(
        part.get("text", "") if isinstance(part, dict) else getattr(part, "text", "")
        for part in getattr(request, "extra_user_content_parts", [])
    )


def test_current_human_message_is_anchored_after_old_examples_without_losing_media(
    config, diversity_history,
):
    router = GroupChatRouter(config)
    event = Event("主动消息", message_id="new-topic")
    event.is_at_or_wake_command = True
    assert router.observe_event(event) == "direct"
    event.message_str = "处理器临时改写的旧话题"
    injected = SimpleNamespace(
        text="<context>\n群友习惯资料\n[HIDDEN_INSTRUCTION_START]\n" + diversity_history
        + "\n[HIDDEN_INSTRUCTION_END]\n保留其他表达学习\n</context>",
        _no_save=True,
    )
    media = SimpleNamespace(text="<video_evidence>本轮实际视频证据</video_evidence>", _no_save=True)
    actual_history = [{"role": "assistant", "content": "真实对话里原来的寒冰屏障回答"}]
    request = SimpleNamespace(
        system_prompt="已有角色和记忆", prompt="当前请求正文原样保留",
        contexts=copy.deepcopy(actual_history), extra_user_content_parts=[injected, media],
    )
    router.prepare_request(event, request)
    text = _all_request_text(request)
    assert '"current_human_message": "主动消息"' in text
    assert "旧问题或旧Bot答案代替" in text and "先处理本次纠正与追问" in text
    assert "上一轮错误示例" not in text
    assert "群友习惯资料" in text and "保留其他表达学习" in text
    assert "本轮实际视频证据" in text
    assert request.contexts == actual_history
    assert request.prompt == "当前请求正文原样保留"
    assert request.extra_user_content_parts[0] is not injected
    assert "上一轮错误示例" in injected.text  # 原插件缓存不被本轮视图修改。
    assert request.extra_user_content_parts[1] is media
    before = _all_request_text(request)
    router.prepare_request(event, request)
    assert _all_request_text(request) == before


def test_exact_diversity_example_in_legacy_system_is_removed_but_actual_history_kept(config, diversity_history):
    router = GroupChatRouter(config)
    event = Event("寒冰屏障不是狂野的吗？")
    assert router.observe_event(event) == "question"
    request = SimpleNamespace(
        system_prompt="角色前缀\n[HIDDEN_INSTRUCTION_START]\n" + diversity_history + "\n[HIDDEN_INSTRUCTION_END]\n角色后缀",
        contexts=[{"role": "user", "content": diversity_history}], prompt="当前问题",
    )
    router.prepare_request(event, request)
    assert "上一轮错误示例" not in request.system_prompt
    assert "角色前缀" in request.system_prompt and "角色后缀" in request.system_prompt
    assert "[HIDDEN_INSTRUCTION_START]" in request.system_prompt
    assert request.contexts[0]["content"] == diversity_history
    assert "寒冰屏障不是狂野的吗" in _all_request_text(request)


@pytest.mark.parametrize("system", [
    "【你最近的回复历史】\n1. 这是普通资料，格式并非SelfLearning注入模板。",
    "参考这些回复，避免使用相同的开场白、结尾、句式和表达方式：\n1. 没有起止边界。",
])
def test_unmatched_history_and_non_temporary_parts_are_unchanged(config, diversity_history, system):
    router = GroupChatRouter(config)
    event = Event("这个功能怎么用？")
    router.observe_event(event)
    quoted = SimpleNamespace(text="<context>\n" + diversity_history + "\n</context>", _no_save=False)
    request = SimpleNamespace(system_prompt=system, extra_user_content_parts=[quoted])
    router.prepare_request(event, request)
    assert request.system_prompt.startswith(system)
    assert request.extra_user_content_parts[0] is quoted
    assert quoted.text == "<context>\n" + diversity_history + "\n</context>"


def test_out_of_scope_request_has_no_anchor_or_example_cleaning(config, diversity_history):
    event = Event("主动消息", group="other-group")
    event.set_extra("qqchat_group_reply_reason", "direct")
    request = SimpleNamespace(system_prompt=diversity_history, extra_user_content_parts=[])
    GroupChatRouter(config).prepare_request(event, request)
    assert request.system_prompt == diversity_history and request.extra_user_content_parts == []


@pytest.mark.parametrize("text", [
    "原神牛逼吗", "这个新产品牛不牛", "这个方案牛b？", "你觉得这个功能厉不厉害？",
    "怎么评价这次表现", "如何评价这个设计", "这技能牛B",
])
def test_explicit_evaluation_focus_is_generic_and_latest_question_is_last(config, text):
    router = GroupChatRouter(config)
    event = Event(text)
    event.is_at_or_wake_command = True
    router.observe_event(event)
    request = SimpleNamespace(system_prompt="既有人格", contexts=[
        {"role": "assistant", "content": "此前关于完全不同对象的旧观点"},
    ])
    router.prepare_request(event, request)
    prompt = _all_request_text(request)
    assert "【本轮评价重点】" in prompt
    assert "首句先明确给出自己的立场和一个具体依据" in prompt
    assert "开头不要介绍这个对象的定义" in prompt
    assert "不能编资料、数字或近况" in prompt
    assert prompt.rstrip().endswith("【现在回答】" + text)
    assert request.contexts[0]["content"] == "此前关于完全不同对象的旧观点"


@pytest.mark.parametrize("text", ["主动消息", "推荐一套卡组", "怎么评价是什么意思", "把牛b翻译成英文"])
def test_evaluation_focus_does_not_leak_from_history_or_replace_other_intents(config, text):
    router = GroupChatRouter(config)
    event = Event(text)
    event.is_at_or_wake_command = True
    router.observe_event(event)
    request = SimpleNamespace(system_prompt="角色", contexts=[{"role": "user", "content": "原神牛逼吗"}])
    router.prepare_request(event, request)
    prompt = _all_request_text(request)
    assert "【本轮评价重点】" not in prompt
    assert prompt.rstrip().endswith("【现在回答】" + text)


def test_only_same_human_can_continue_and_bot_echo_cannot_continue_itself(config):
    now = [1000.0]
    router = GroupChatRouter(config, clock=lambda: now[0])
    question = Event("这游戏怎么玩？", message_id="1")
    router.observe_event(question)
    router.finish_event(question, sent=True)
    assert router.observe_event(Event("对，我试过了", message_id="2")) == "followup"
    assert router.observe_event(Event("对，我也试过了", user="200", message_id="3")) is None
    assert router.observe_event(Event("我再讲讲", user="3503565007", message_id="4")) is None
    assert router.observe_event(Event("好的", message_id="5")) is None
    now[0] += 181
    assert router.observe_event(Event("另一个话题", message_id="6")) is None


def test_zero_followup_window_disables_followups_even_with_same_clock_tick(config):
    config["group_chat_followup_seconds"] = 0
    router = GroupChatRouter(config, clock=lambda: 1000)
    first = Event("这个游戏怎么样？", message_id="1")
    router.observe_event(first)
    router.finish_event(first, sent=True)
    assert router.observe_event(Event("我试过了", message_id="2")) is None


def test_ignored_bots_unconfigured_groups_and_nonqq_are_not_changed(config):
    config["group_chat_ignored_user_ids"] = ["robot2"]
    router = GroupChatRouter(config)
    for event in (Event("群分析", group="other"), Event("群分析", platform="weixin_oc")):
        assert not router.prepare_event(event)
        assert router.observe_event(event) is None
        assert event.message_str == "群分析" and not event.is_at_or_wake_command
    bot = Event("群分析", user="robot2")
    assert router.prepare_event(bot)
    assert not bot.is_at_or_wake_command
    assert router.observe_event(bot) is None


def test_command_and_chat_switches_work_independently(config):
    config["group_chat_enabled"] = False
    router = GroupChatRouter(config)
    event = Event("群漫画")
    assert router.prepare_event(event) and event.is_at_or_wake_command
    assert router.observe_event(event) == "command"
    assert router.observe_event(Event("怎么配队？", message_id="2")) is None
    config["natural_commands_enabled"] = False
    config["group_chat_enabled"] = True
    untouched = Event("群漫画", message_id="3")
    assert router.prepare_event(untouched)
    assert untouched.message_str == "群漫画" and not untouched.is_at_or_wake_command


@pytest.mark.parametrize("text", [
    "这个角色厉不厉害", "原神牛不牛逼", "评价一下这游戏", "给我推荐一下配队",
    "有没有什么建议", "这个报错怎么办", "帮我看看这个视频", "总结一下这个视频",
])
def test_opinions_advice_and_media_requests_wake_without_at(config, text):
    event = Event(text)
    router = GroupChatRouter(config)
    router.prepare_event(event)
    assert not event.get_extra("qqchat_natural_command")
    assert router.observe_event(event) == "question"


@pytest.mark.parametrize("allowed", [True, False])
async def test_plugin_route_checks_old_acl_before_waking_and_still_observes_once(
    monkeypatch, config, allowed,
):
    from test_astrbot_plugin import _load_plugin

    module = _load_plugin(monkeypatch)
    plugin = module.QQChatPlugin(object(), config)
    plugin.runtime = object()
    calls = []

    async def allowed_event(raw):
        calls.append(raw)
        return allowed

    monkeypatch.setattr(plugin, "_legacy_event_allowed", allowed_event)
    event = Event("有什么建议？")
    await plugin.route_native_group_message(event)
    assert len(calls) == 1
    if allowed:
        assert not event.stopped and event.is_at_or_wake_command
        await plugin.route_native_group_message(event)
        assert len(plugin._get_group_router()._state(event).seen) == 1
    else:
        assert event.stopped and event.llm_calls == [False]
        assert not event.is_at_or_wake_command


async def test_discussing_replay_command_cannot_fall_through_to_legacy_execution(monkeypatch, config):
    from test_astrbot_plugin import _load_plugin

    module = _load_plugin(monkeypatch)
    plugin = module.QQChatPlugin(object(), config)
    event = Event("牌谱分析 怎么用")
    router = plugin._get_group_router()
    router.prepare_event(event)
    assert event.get_extra("qqchat_skip_local_command")
    calls = []

    async def handle_event(*args):
        calls.append(args)
        raise AssertionError("讨论命令不能执行旧入口")

    plugin.runtime = SimpleNamespace(handle_event=handle_event)
    await plugin.on_onebot_message(event)
    assert not calls


def test_explicit_command_parameters_are_not_reinterpreted_after_native_slash_removal(config):
    text = '/牌谱分析 座位=东 赛制=半庄 JSON={"description":"不要改写输入"}'
    event = Event(text)
    event.message_str = text.removeprefix("/")
    event.is_at_or_wake_command = True
    router = GroupChatRouter(config)
    router.prepare_event(event)
    assert event.message_str == text.removeprefix("/")
    assert not event.get_extra("qqchat_skip_local_command")
    assert normalized_runtime_event(event, event.message_obj.raw_message) is event.message_obj.raw_message
