from app.services.possession_style import history_message_text, member_style_samples


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
