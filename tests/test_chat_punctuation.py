from app.services.chat_punctuation import without_final_period


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
    from types import SimpleNamespace, ModuleType
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
    chain = [Plain("第一句。"), Plain("最后一句。"), SimpleNamespace(type="image")]
    event = SimpleNamespace(get_platform_name=lambda: "aiocqhttp", get_group_id=lambda: "42",
                            get_result=lambda: SimpleNamespace(chain=chain))
    observed = []

    async def voice(runtime, event):
        observed.append(chain[1].text)

    monkeypatch.setattr("app.astrbot_chat.decorate_native_voice", voice)
    await plugin.filter_qq_source_links(event)
    assert chain[0].text == "第一句。"
    assert observed == ["最后一句"]
    chain[1].text = "私聊。"
    event.get_group_id = lambda: ""
    await plugin.filter_qq_source_links(event)
    assert chain[1].text == "私聊。"
