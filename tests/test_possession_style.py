import pytest

import app.services.possession_style as possession_style_module
from app.services.possession_style import (
    history_message_text,
    image_references_from_message,
    learn_possession_style,
    member_media_style_marker,
    member_style_image_refs,
    member_style_samples,
    style_catchphrases,
    style_reference_examples,
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
