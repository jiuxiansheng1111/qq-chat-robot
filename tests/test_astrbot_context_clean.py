from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.astrbot_context_clean import clean_managed_group_bot_history

MARKER = "<!-- private_companion_group_context_v1 -->"
HUMAN = '<message role="user">真实人类 &amp; 原话</message>'
BOT = '<message role="assistant">旧Bot答复<seg>分段</seg></message>'
GROUP = (
    '<section title="群聊上下文"><group_context><current>当前人类</current><history>'
    + HUMAN
    + BOT
    + "</history><scene>氛围</scene><context>图像依据</context><constraints>限制</constraints></group_context></section>"
)
QUOTE = '<section title="本轮引用卡片/动态"><![CDATA[真实引用原文<message role="assistant">不能删</message>]]></section>'


def request(text=GROUP + "\n\n" + QUOTE, *, marker=MARKER, managed=True):
    part = SimpleNamespace(type="text", text=text, _private_companion_turn_fragments=managed)
    return SimpleNamespace(
        prompt="当前用户正文",
        system_prompt="原系统提示" + GROUP,
        extra_user_content_parts=[part],
        _private_companion_turn_prompt_fragments=[{"marker": marker, "content": GROUP}],
    )


def test_only_controlled_assistant_history_removed_from_copy():
    req = request()
    original = req.extra_user_content_parts[0]
    image = SimpleNamespace(type="image_url", image_url={"url": "real-media"})
    plain_quote = SimpleNamespace(type="text", text=GROUP)
    req.extra_user_content_parts.extend([image, plain_quote])
    assert clean_managed_group_bot_history(req) == 1
    assert req.extra_user_content_parts[0] is not original
    assert original.text == GROUP + "\n\n" + QUOTE
    assert req.extra_user_content_parts[0].text == GROUP.replace(BOT, "") + "\n\n" + QUOTE
    assert req.extra_user_content_parts[1] is image
    assert req.extra_user_content_parts[2] is plain_quote and plain_quote.text == GROUP
    assert req.prompt == "当前用户正文" and req.system_prompt == "原系统提示" + GROUP
    assert req._private_companion_turn_prompt_fragments[0]["content"] == GROUP
    assert clean_managed_group_bot_history(req) == 0


@pytest.mark.parametrize("marker,managed", [("wrong-marker", True), (MARKER, False)])
def test_missing_control_boundary_preserves_everything(marker, managed):
    req = request(marker=marker, managed=managed)
    original_parts = req.extra_user_content_parts
    assert clean_managed_group_bot_history(req) == 0
    assert req.extra_user_content_parts is original_parts


@pytest.mark.parametrize(
    "text",
    [
        GROUP.replace("</history>", ""),
        GROUP + "<unclosed>",
        '<!DOCTYPE data [<!ENTITY example "expansion">]>' + GROUP,
        '<section title="其他区域"><group_context><history>'
        + BOT
        + "</history></group_context></section>",
        '<section title="群聊上下文"><other><history>' + BOT + "</history></other></section>",
        '<section title="本轮引用卡片/动态"><![CDATA[' + GROUP + "]]></section>",
    ],
)
def test_invalid_xml_or_foreign_scope_is_not_partially_rewritten(text):
    req = request(text)
    assert clean_managed_group_bot_history(req) == 0
    assert req.extra_user_content_parts[0].text == text


def test_plan_metadata_marker_works_without_raw_marker_text():
    req = request()
    req._private_companion_turn_prompt_fragments = []
    block = SimpleNamespace(
        key="group.context", marker="", metadata={"delivery_group_marker": MARKER}
    )
    req._private_companion_conversation_injection_plan = SimpleNamespace(blocks=lambda: [block])
    assert MARKER not in req.extra_user_content_parts[0].text
    assert clean_managed_group_bot_history(req) == 1
    assert block.metadata == {"delivery_group_marker": MARKER}


def test_empty_message_and_quoted_angle_bracket_attributes_preserve_neighbors():
    first = '<message role="assistant" hint="a > b" />'
    second = '<message role="assistant">旧文</message>'
    text = GROUP.replace(BOT, first + second)
    req = request(text)
    assert clean_managed_group_bot_history(req) == 2
    assert req.extra_user_content_parts[0].text == text.replace(first, "").replace(second, "")


def test_native_text_part_copy_keeps_private_flags():
    module = pytest.importorskip("astrbot.core.agent.message")
    part = module.TextPart(text=GROUP).mark_as_temp()
    part._private_companion_turn_fragments = True
    req = request()
    req.extra_user_content_parts = [part]
    assert clean_managed_group_bot_history(req) == 1
    copied = req.extra_user_content_parts[0]
    assert copied.text == GROUP.replace(BOT, "") and part.text == GROUP
    assert copied._no_save is True and copied._private_companion_turn_fragments is True
