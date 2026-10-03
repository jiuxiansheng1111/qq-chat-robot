import pytest

from app.services.voice_persona import (
    Invocation,
    character_invocation,
    has_voice_persona,
    resolve_voice_persona_alias,
)

PROFILES = {
    "murasame": {"label": "小丛雨"},
    "yoshino": {"label": "芳乃"},
    "mako": {"label": "茉子"},
    "aimisi": {"label": "爱弥斯"},
    "lena": {"label": "蕾娜"},
    "roka": {"label": "芦花"},
    "koharu": {"label": "小春"},
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("芳乃", Invocation("yoshino", "")),
        ("朝武芳乃", Invocation("yoshino", "")),
        ("叫茉子出来一下", Invocation("mako", "")),
        ("芳乃出来一下", Invocation("yoshino", "")),
        ("芳乃你出来一下", Invocation("yoshino", "")),
        ("芦花姐，陪我聊聊", Invocation("roka", "陪我聊聊")),
        ("芦花姐陪我聊聊", Invocation("roka", "陪我聊聊")),
        ("飞行雪绒，陪我说说话", Invocation("aimisi", "陪我说说话")),
        ("切换到蕾娜", Invocation("lena", "")),
        ("切换到角色 茉子", Invocation("mako", "")),
    ],
)
def test_explicit_character_invocations(text, expected):
    assert character_invocation(text, PROFILES) == expected


@pytest.mark.parametrize(
    "text",
    [
        "介绍一下芳乃",
        "请介绍一下芳乃",
        "我喜欢芳乃",
        "昨天看了芳乃的介绍",
        "芳乃是谁",
        "芳乃，介绍一下她的设定",
        "别叫芳乃出来",
        "不要切换到芳乃",
        "叫芳乃还是茉子出来",
        "我不想切换到芳乃",
    ],
)
def test_character_mentions_without_an_unambiguous_call_do_not_switch(text):
    assert character_invocation(text, PROFILES) is None


def test_role_selection_uses_the_same_full_and_short_aliases_as_invocation():
    assert resolve_voice_persona_alias("朝武芳乃", PROFILES) == "yoshino"
    assert resolve_voice_persona_alias("巫女姬", PROFILES) == "yoshino"
    assert resolve_voice_persona_alias("芦花姐", PROFILES) == "roka"
    assert resolve_voice_persona_alias("小春", PROFILES) == "koharu"
    assert resolve_voice_persona_alias("rena", PROFILES) == "lena"


def test_legacy_rena_profile_id_uses_lena_persona_and_aliases():
    profiles = {"rena": {"label": "蕾娜"}}

    assert has_voice_persona("rena")
    assert character_invocation("蕾娜", profiles) == Invocation("rena", "")
    assert resolve_voice_persona_alias("lena", profiles) == "rena"
