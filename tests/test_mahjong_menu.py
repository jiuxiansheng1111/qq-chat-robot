"""The actual image/text menu includes the feature without removing character catalogs."""

from types import SimpleNamespace

import pytest
from PIL import Image

from app.services import help_menu


def test_local_feature_menu_has_triggers_inputs_and_honest_link_limit():
    image = {name: lines for name, lines, _ in help_menu._IMAGE_GROUPS}
    text = help_menu.concise_text_menu()
    assert "雀魂牌谱分析" in image
    assert any("链接仅识别" in line for line in image["雀魂牌谱分析"])
    for entry in (
        "帮我分析牌谱",
        "分析雀魂牌谱",
        "帮我分析一下这个对局",
        "已结束牌谱",
        "开局固定座位",
        "mjai",
        "tenhou.net/6",
        "不能直接抓取解析",
    ):
        assert entry in text
    for original in ("角色图鉴", "今日奥特曼", "翻唱", "天气", "点歌"):
        assert original in text


async def test_real_menu_render_and_cache_invalidation(tmp_path, monkeypatch):
    monkeypatch.setattr(help_menu, "_PROJECT_ROOT", tmp_path)
    try:
        help_menu._find_font_path()
    except RuntimeError as exc:
        pytest.skip(f"真实菜单渲染需要本机中文字体，当前环境未提供：{exc}")
    settings = SimpleNamespace(help_menu_background_path=tmp_path / "none.png")
    first = await help_menu.prepare_help_menu_file(settings)
    with Image.open(first) as rendered:
        assert rendered.width == 1080 and rendered.height >= 1480
        assert rendered.format in {"PNG", "JPEG"}
    assert first.exists() and first.stat().st_size > 1000
    assert await help_menu.prepare_help_menu_file(settings) == first
    monkeypatch.setattr(help_menu, "_CACHE_VERSION", "mahjong-test-new-version")
    second = await help_menu.prepare_help_menu_file(settings)
    assert second != first and second.exists()
