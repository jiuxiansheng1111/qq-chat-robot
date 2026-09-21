import pytest

import app.services.possession_style as possession_style_module
from app.services.possession_style import (
    history_message_text,
    image_references_from_message,
    learn_possession_style,
    member_media_style_marker,
    member_style_image_refs,
    member_style_samples,
    possession_recall_prompt,
    possession_recall_terms,
    select_possession_recall_evidence,
    style_catchphrases,
    style_reference_examples,
    summarize_possession_recall,
)


def test_history_message_text_keeps_plain_text_only():
    message = [
        {"type": "at", "data": {"qq": "1"}},
        {"type": "text", "data": {"text": "  哈哈  "}},
        {"type": "image", "data": {"file": "secret.jpg"}},
        {"type": "text", "data": {"text": "确实 "}},
    ]
    assert history_message_text(message) == "哈哈 确实"
    assert history_message_text("[CQ:at,qq=1] 你好") == "你好"


def test_member_style_samples_filters_other_members_and_rich_messages():
    payload = {
        "data": {
            "messages": [
                {"user_id": 7, "message": "短"},
                {"user_id": 8, "message": "这是别人的消息"},
                {"sender": {"user_id": 7}, "message": "哈哈，确实"},
                {
                    "sender": {"user_id": "7"},
                    "message": [{"type": "text", "data": {"text": "行吧行吧"}}],
                },
                {"user_id": 7, "message": "https://example.com"},
            ]
        }
    }
    assert member_style_samples(payload, "7", 10) == ["哈哈，确实", "行吧行吧"]


def test_style_reference_examples_keep_rhythm_but_drop_identity_facts():
    assert style_reference_examples(
        [
            "压根不行",
            "我是 hzh",
            "爸爸是谁",
            "压根不行",
            "怎么这样",
            "https://example.com",
        ]
    ) == ["压根不行", "怎么这样"]


def test_media_marker_and_catchphrases_are_learned_without_image_replay():
    payload = {
        "data": {
            "messages": [
                {
                    "user_id": "7",
                    "message": [{"type": "image", "data": {"file": "private.jpg"}}],
                },
                {"user_id": "7", "message": "这也太有乐子了，老爸"},
                {"user_id": "8", "message": [{"type": "image", "data": {}}]},
            ]
        }
    }
    marker = member_media_style_marker(payload, "7")
    assert "1 条含图片或表情" in marker
    assert "private.jpg" not in marker
    assert style_catchphrases(["这也太有乐子了，老爸"]) == ["乐子", "老爸"]


def test_target_image_references_are_selected_without_local_paths():
    payload = {
        "data": {
            "messages": [
                {
                    "user_id": "7",
                    "message": [
                        {"type": "image", "data": {"file": "sent-image.png"}},
                        {"type": "image", "data": {"file": "C:\\private\\photo.png"}},
                    ],
                },
                {
                    "user_id": "7",
                    "message": [
                        {"type": "image", "data": {"url": "https://example.com/a.jpg"}}
                    ],
                },
                {
                    "user_id": "8",
                    "message": [
                        {"type": "image", "data": {"file": "other-user.png"}}
                    ],
                },
            ]
        }
    }
    assert image_references_from_message(payload["data"]["messages"][0]["message"]) == [
        "sent-image.png"
    ]
    assert member_style_image_refs(payload, "7") == [
        "sent-image.png",
        "https://example.com/a.jpg",
    ]


@pytest.mark.asyncio
async def test_style_learning_falls_back_to_messages_observed_by_webhook(monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("NapCat history unavailable")

    class FakeDatabase:
        saved = None

        async def possession_style_profile(self, *args):
            return None

        async def save_possession_style_profile(self, *args):
            self.saved = args

    class FakeLLM:
        async def ask(self, messages):
            assert "压根不行" in messages[1]["content"]
            return "短句、直接、口语化。"

    settings = type(
        "Settings",
        (),
        {"possession_style_refresh_hours": 24, "possession_style_sample_limit": 30},
    )()
    database = FakeDatabase()
    monkeypatch.setattr(
        possession_style_module, "fetch_member_style_samples", unavailable
    )

    samples = await learn_possession_style(
        settings,
        database,
        FakeLLM(),
        "group",
        "member",
        "羽入",
        force_refresh=True,
        fallback_samples=["压根不行", "怎么这样", "发不出来了"],
    )

    assert samples == ["压根不行", "怎么这样", "发不出来了"]
    assert database.saved is not None


def test_possession_recall_terms_extract_people_objects_and_intents():
    subjects, intents = possession_recall_terms("你们认识山乃乃吗")
    assert subjects == ["山乃乃"]
    assert "认识" in intents
    assert "朋友" in intents

    subjects, intents = possession_recall_terms("你觉得苹果手机怎么样")
    assert subjects == ["苹果手机"]
    assert "感觉" in intents

    subjects, intents = possession_recall_terms("你最喜欢哪个角色")
    assert subjects == []
    assert "喜欢" in intents
    assert "本命" in intents


def test_possession_recall_evidence_prioritizes_subject_and_keeps_nearby_context():
    samples = [
        "今天好困",
        "山乃乃又来了",
        "我最喜欢干山乃乃",
        "一拳打的山乃乃",
        "晚饭吃啥",
        "我最近在玩原神",
    ]
    evidence = select_possession_recall_evidence(
        "你们认识山乃乃吗",
        samples,
        limit=6,
    )
    assert "山乃乃又来了" in evidence
    assert "我最喜欢干山乃乃" in evidence
    assert "一拳打的山乃乃" in evidence
    assert "我最近在玩原神" not in evidence


def test_possession_recall_excludes_current_question_from_evidence():
    question = "你认识山乃乃吗"
    evidence = select_possession_recall_evidence(
        question,
        [question, "山乃乃老是艾特我", "不知道"],
    )
    assert question not in evidence
    assert "山乃乃老是艾特我" in evidence


def test_possession_recall_can_answer_generic_preference_question():
    evidence = select_possession_recall_evidence(
        "你最喜欢哪个角色",
        ["随便", "我本命是初音未来", "最近天气不错"],
    )
    assert evidence == ["我本命是初音未来"]


def test_possession_recall_prompt_is_non_verbatim_fallback():
    source = ["山乃乃怎么又来了", "我最喜欢干山乃乃"]
    prompt = possession_recall_prompt(
        "羽入",
        "你觉得山乃乃怎么样",
        source,
    )
    assert "羽入的相关历史认知摘要" in prompt
    assert "山乃乃" in prompt
    assert "不能据此推断现实中的朋友" in prompt
    assert "不要引用、复述" in prompt
    assert all(item not in prompt for item in source)


@pytest.mark.asyncio
async def test_possession_recall_summary_only_exposes_semantic_digest():
    class FakeLLM:
        async def ask(self, messages):
            assert "我最喜欢干山乃乃" in messages[1]["content"]
            return (
                '{"knowledge":"过去多次提到山乃乃，对这个名字并不陌生",'
                '"attitude":"相关表达带有明显调侃意味",'
                '"relationship":"无法从群聊判断现实中是否认识",'
                '"uncertainty":"具体关系和真实态度仍不确定"}'
            )

    result = await summarize_possession_recall(
        "羽入",
        "你认识山乃乃吗",
        ["山乃乃怎么又来了", "我最喜欢干山乃乃", "一拳打的山乃乃"],
        FakeLLM(),
    )
    assert "并不陌生" in result
    assert "调侃意味" in result
    assert "我最喜欢干山乃乃" not in result
    assert "一拳打的山乃乃" not in result
    assert "不要引用历史原句" in result


@pytest.mark.asyncio
async def test_possession_recall_summary_drops_model_parroting_and_falls_back():
    class ParrotingLLM:
        async def ask(self, messages):
            return (
                '{"knowledge":"我最喜欢干山乃乃",'
                '"attitude":"一拳打的山乃乃",'
                '"relationship":"",'
                '"uncertainty":""}'
            )

    source = ["山乃乃怎么又来了", "我最喜欢干山乃乃", "一拳打的山乃乃"]
    result = await summarize_possession_recall(
        "羽入",
        "你认识山乃乃吗",
        source,
        ParrotingLLM(),
    )
    assert "山乃乃" in result
    assert "我最喜欢干山乃乃" not in result
    assert "一拳打的山乃乃" not in result
    assert "完全没听过" in result
