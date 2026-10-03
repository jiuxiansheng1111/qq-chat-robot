import pytest

from app.services.character_catalog import CHARACTER_CATALOG_COMMANDS, extract_catalog_lookup
from app.services.help_menu import is_help_menu_request, is_text_menu_request
from app.services.menu_intent import normalize_menu_intent
from app.services.short_intent import canonicalize_short_command
from app.services.singing import parse_singing_command


@pytest.mark.parametrize(
    ("phrase", "command"),
    [
        ("给我看看菜单", "帮助"),
        ("帮我看菜单", "帮助"),
        ("功能菜单给我发一下", "帮助"),
        ("功能", "帮助"),
        ("帮忙", "帮助"),
        ("你会什么", "帮助"),
        ("我想看文字版菜单", "文字版菜单"),
        ("发我一份文字菜单吧", "文字版菜单"),
        ("发个猫咪动图呗", "随机猫咪"),
        ("给我猫咪动图", "随机猫咪"),
        ("来一个随机猫图", "随机猫咪"),
        ("来段翻唱", "翻唱片段"),
        ("来段翻唱 芳乃 春泥泥", "翻唱片段 芳乃 春泥泥"),
        ("我想看角色图鉴", "角色图鉴"),
        ("我想看角色图鉴里的捷德", "角色图鉴 捷德"),
        ("我想看奥特曼图鉴里的迪迦", "查询奥特曼 迪迦"),
        ("让我看看可用角色", "可用角色"),
        ("语音开一下", "启动语音"),
        ("语音关一下", "关闭语音"),
        ("语音开着吗", "语音状态"),
        ("帮我选择角色 芳乃", "选择角色 芳乃"),
        ("我想切换到芳乃", "选择角色 芳乃"),
    ],
)
def test_short_natural_requests_map_to_existing_commands(phrase, command):
    assert normalize_menu_intent(phrase) == command


@pytest.mark.parametrize(
    "command",
    [
        "/help",
        "帮助",
        "/菜单",
        "随机猫咪",
        "关闭语音",
        "可用角色",
        "/选择角色",
        "选择角色 芳乃",
        "/角色图鉴",
        "角色图鉴 捷德",
    ],
)
def test_existing_commands_and_arguments_are_preserved(command):
    assert normalize_menu_intent(command) == command


@pytest.mark.parametrize(
    ("phrase", "command"),
    [
        ("/给我看看菜单", "/帮助"),
        ("/给我猫咪动图", "/猫"),
        ("/来一个随机猫图", "/猫"),
        ("/来段翻唱 春泥泥", "/翻唱片段 春泥泥"),
        ("/我想看角色图鉴", "/角色图鉴"),
        ("/帮我选择角色 芳乃", "/选择角色 芳乃"),
    ],
)
def test_slash_short_requests_keep_route_prefix(phrase, command):
    assert normalize_menu_intent(phrase) == command


def test_empty_chart_commands_still_open_catalog_overview():
    for command in ("/图鉴", "/角色图鉴"):
        assert normalize_menu_intent(command) == command
        assert command in CHARACTER_CATALOG_COMMANDS


@pytest.mark.parametrize(
    "phrase",
    [
        "不要发猫图",
        "别打开语音",
        "不要打开语音",
        "不要关闭语音",
        "我不想看角色图鉴",
    ],
)
def test_negative_requests_are_not_converted(phrase):
    assert normalize_menu_intent(phrase) == phrase


def test_close_voice_wins_when_both_actions_are_named():
    assert normalize_menu_intent("语音开一下，还是关一下") == "关闭语音"
    assert normalize_menu_intent("语音开着吗，还是关一下") == "关闭语音"


@pytest.mark.parametrize(
    "phrase",
    [
        "我喜欢菜单的设计",
        "猫咪动图很好看",
        "听说角色图鉴更新了",
        "语音听起来很可爱",
        "选一个角色看看",
        "这段聊天提到了菜单，但我只是在闲聊并且句子很长" * 2,
    ],
)
def test_unrequested_or_long_text_stays_unchanged(phrase):
    assert normalize_menu_intent(phrase) == phrase


def test_chart_query_parameters_remain_parseable():
    normalized = normalize_menu_intent("我想看角色图鉴里的雷姆")
    assert normalized == "角色图鉴 雷姆"
    assert extract_catalog_lookup(normalized) == ("all", "雷姆")
    assert canonicalize_short_command(normalized, addressed=True) == normalized


def test_voice_role_argument_survives_existing_normalizer():
    normalized = normalize_menu_intent("帮我选择角色 芳乃")
    assert normalized == "选择角色 芳乃"
    assert canonicalize_short_command(normalized, addressed=True) == normalized


@pytest.mark.parametrize(
    ("request_text", "addressed", "query"),
    [
        ("翻唱片段 芳乃 春泥泥", True, "春泥泥"),
        ("/翻唱片段 春泥泥", False, "春泥泥"),
    ],
)
def test_natural_cover_phrase_reaches_clip_parser(request_text, addressed, query):
    normalized = normalize_menu_intent(request_text.replace("翻唱片段", "来段翻唱", 1))
    parsed = parse_singing_command(
        normalized, {"yoshino": {"label": "芳乃"}}, addressed=addressed
    )
    assert parsed is not None
    assert parsed.action == "sing"
    assert parsed.mode == "clip"
    assert parsed.query == query


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("帮助", True),
        ("给我看看菜单", True),
        ("帮我看菜单", True),
        ("功能菜单给我发一下", True),
        ("功能", True),
        ("帮忙", True),
        ("你会什么", True),
        ("不要给我菜单", False),
        ("菜单设计得很好", False),
        ("我想看文字版菜单", False),
        ("文字菜单给我看看", False),
    ],
)
def test_help_image_request_detection(phrase, expected):
    assert is_help_menu_request(phrase) is expected


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("文字版菜单", True),
        ("/help 文字版菜单", True),
        ("给我看看文字版菜单", True),
        ("帮我看文字菜单", True),
        ("发我一份文字菜单吧", True),
        ("不要给我文字菜单", False),
        ("文字菜单风格很好", False),
    ],
)
def test_text_menu_detection_accepts_natural_requests(phrase, expected):
    assert is_text_menu_request(phrase) is expected
