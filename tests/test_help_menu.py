import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.plugins.registry import registry
from app.services import help_menu
from app.services.character_catalog import extract_catalog_lookup
from app.services.singing import parse_singing_command


@pytest.mark.parametrize(
    "text",
    [
        "文字版菜单",
        "请给我文字版菜单",
        "文字菜单",
        "文字版帮助",
        "菜单文字版",
        "/文字版菜单",
        "/help 文字版菜单",
        "/帮助 文字版帮助",
        "/功能菜单 菜单文字版",
    ],
)
def test_explicit_text_menu_requests(text):
    assert help_menu.is_text_menu_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "我喜欢文字菜单风格",
        "文字版菜单怎么写",
        "我想聊聊文字",
        "/help 文字版菜单 详细说明",
        None,
    ],
)
def test_casual_text_does_not_request_menu(text):
    assert not help_menu.is_text_menu_request(text)


def test_image_menu_covers_chart_and_cover_modes():
    groups = {heading: lines for heading, lines, _ in help_menu._IMAGE_GROUPS}
    assert set(groups) == {
        "聊天陪伴", "语音翻唱", "点歌视频", "实用工具", "互动小游戏", "管理"
    }
    assert all(len(lines) <= 3 for lines in groups.values())
    assert any("图鉴" in line and "角色图鉴" in line for line in groups["互动小游戏"])
    assert len(groups["语音翻唱"]) == 3
    assert "选择角色" in groups["语音翻唱"][0]
    assert "翻唱片段 芳乃 歌名" in groups["语音翻唱"][1]
    assert "约20秒" in groups["语音翻唱"][1]
    assert "翻唱完整 芳乃 歌名" in groups["语音翻唱"][2]
    assert "2分钟" in groups["语音翻唱"][2]


def test_text_menu_lists_real_feature_examples():
    menu = help_menu.concise_text_menu()
    for section in ("聊天陪伴", "语音翻唱", "点歌视频", "实用工具", "互动小游戏", "管理"):
        assert f"【{section}】" in menu
    for example in (
        "启动语音", "关闭语音", "翻唱片段 芳乃 歌名", "翻唱完整 芳乃 歌名",
        "上限115秒", "点歌", "播放视频", "天气 上海", "随机猫咪", "随机猪猪",
        "随机奶龙", "今日热点", "生成图片", "今日奥特曼", "随机二次元角色",
        "@我 图鉴 / 角色图鉴", "@我 角色图鉴 捷德（也可换成“雷姆”）", "随机夺舍", "恋爱模式",
        "记住：内容", "/bot on", "/blacklist add|remove",
    ):
        assert example in menu


@pytest.mark.parametrize(
    ("command", "plugin"),
    [
        ("图鉴", "character_catalog"),
        ("角色图鉴", "character_catalog"),
        ("今日奥特曼", "ultraman"),
        ("随机二次元角色", "anime_character"),
        ("/点歌", "music"),
        ("/视频", "bilibili_video"),
        ("/天气", "weather"),
        ("随机猫咪", "cat"),
        ("随机猪猪", "pig"),
        ("随机奶龙", "nailong"),
        ("/bot", "group_admin"),
        ("/blacklist", "group_admin"),
    ],
)
def test_menu_commands_are_registered(command, plugin):
    spec = registry.find(command)
    assert spec is not None
    assert spec.name == plugin


@pytest.mark.parametrize("name", ["捷德", "雷姆"])
def test_catalog_text_examples_parse_as_single_lookup(name):
    assert extract_catalog_lookup(f"角色图鉴 {name}") == ("all", name)


@pytest.mark.parametrize(
    ("command_text", "mode"),
    [
        ("翻唱片段 芳乃 歌名", "clip"),
        ("翻唱完整 芳乃 歌名", "full"),
    ],
)
def test_cover_examples_are_parseable(command_text, mode):
    parsed = parse_singing_command(
        command_text, {"yoshino": {"label": "芳乃"}}, addressed=True
    )
    assert parsed is not None
    assert parsed.action == "sing"
    assert parsed.mode == mode
    assert parsed.profile_id == "yoshino"
    assert parsed.query == "歌名"


async def test_render_help_menu_returns_cached_onebot_png(tmp_path, monkeypatch):
    monkeypatch.setattr(help_menu, "_PROJECT_ROOT", tmp_path)
    render_calls = []

    def draw(background):
        render_calls.append(background)
        return Image.new("RGB", (1080, 1480), (244, 248, 246))

    monkeypatch.setattr(help_menu, "_draw_menu", draw)
    settings = SimpleNamespace(help_menu_background_path=tmp_path / "missing-background.png")
    first = await help_menu.render_help_menu(settings)
    second = await help_menu.render_help_menu(settings)

    assert first.startswith("base64://")
    assert second == first
    encoded = first.removeprefix("base64://")
    assert len(first.encode("ascii")) <= 4 * 1024 * 1024
    png = base64.b64decode(encoded)
    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == (1080, 1480)
    assert len(render_calls) == 1
    assert list((tmp_path / "data" / "help_menu").glob("menu_*.png"))
