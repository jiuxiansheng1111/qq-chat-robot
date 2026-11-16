import pytest

from app.services.chat_punctuation import (
    natural_chat_text,
    without_final_period,
    without_formatting_stars,
)


def test_only_sentence_ending_changes():
    for original, expected in [
        ("第一句。第二句。", "第一句。第二句"), ("好。\n", "好\n"),
        ("Okay.", "Okay"), ("诶？", "诶？"), ("好！", "好！"),
        ("让我想想...", "让我想想..."), ("嗯……", "嗯……"),
        ("https://example.com/a.", "https://example.com/a."),
        ("版本 1.2.", "版本 1.2."), ("`print('.')`", "`print('.')`"),
    ]:
        assert without_final_period(original) == expected


async def test_qq_group_final_text_is_cleaned_before_voice(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    from test_astrbot_plugin import _load_plugin

    module = _load_plugin(monkeypatch)
    components = ModuleType("astrbot.api.message_components")

    class Plain:
        def __init__(self, text):
            self.text = text

    components.Plain = Plain
    monkeypatch.setitem(sys.modules, components.__name__, components)
    plugin = module.QQChatPlugin(object(), {"legacy_chat": False})
    plugin.runtime = object()
    chain = [Plain("**推荐20牧**。"), Plain("来源：**https://www.hsguru.com/**。"), SimpleNamespace(type="image")]
    event = SimpleNamespace(get_platform_name=lambda: "aiocqhttp", get_group_id=lambda: "42",
                            get_result=lambda: SimpleNamespace(chain=chain))
    observed = []

    async def voice(runtime, event):
        observed.append(chain[1].text)

    monkeypatch.setattr("app.astrbot_chat.decorate_native_voice", voice)
    await plugin.filter_qq_source_links(event)
    assert chain[0].text == "推荐20牧"
    assert "*" not in observed[0]
    chain[1].text = "私聊**卡组**。"
    event.get_group_id = lambda: ""
    await plugin.filter_qq_source_links(event)
    assert chain[1].text == "私聊卡组"
    chain[1].text = "**B站**。"
    event.get_platform_name = lambda: "bilibili"
    await plugin.filter_qq_source_links(event)
    assert chain[1].text == "**B站**。"


@pytest.mark.parametrize("text,expected", [
    ("我推荐**20牧**，*慢速资源*玩法", "我推荐20牧，慢速资源玩法"),
    ("***重点***：**推荐 *火球术*，很强**", "重点：推荐 火球术，很强"),
    ("* **第一项**\n* 第二项\n***\n结束", "• 第一项\n• 第二项\n\n结束"),
    ("**`2**3`**", "`2**3`"),
    ("**来源 https://qq.com/a**", "来源 https://qq.com/a"),
    ("计算2*3*4和2**3**4，a * b * c，牌数火球术*2", "计算2*3*4和2**3**4，a * b * c，牌数火球术*2"),
    ("*.py，glob **/*.py，138****1234", "*.py，glob **/*.py，138****1234"),
    ('“原文**内容**” `a*b`\n```py\nx**2\n```', '“原文**内容**” `a*b`\n```py\nx**2\n```'),
    (r"\*原样\* https://qq.com/q?a=2*3", r"\*原样\* https://qq.com/q?a=2*3"),
])
def test_star_formatting_preserves_code_math_quotes_and_urls(text, expected):
    assert without_formatting_stars(text) == expected
    assert without_formatting_stars(expected) == expected


def test_punctuation_runs_after_star_rendering():
    assert natural_chat_text("**推荐20牧；费用低**。") == "推荐20牧，费用低"
