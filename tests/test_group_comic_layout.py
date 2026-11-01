from __future__ import annotations

import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.services import group_comic_layout as comic
from app.services.local_report_render import find_report_browser


def panel(color="teal", size=(640, 320)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def test_three_real_images_are_embedded_without_avatars_external_resources_or_cropping():
    images = [panel(color) for color in ("teal", "coral", "gold")]
    content, width = comic._layout_html(
        images, ["开端", "变化", "收尾"], "同一个话题", "真实群聊话题"
    )
    assert content.count("<img ") == 3 and content.count("data:image/png;base64,") == 3
    assert "object-fit:contain" in content
    assert all(
        token not in content
        for token in ("<svg", "<script", "<link", "<iframe", "http://", "https://")
    )
    assert "据群聊话题改编" in content and "剧情为创作" in content
    assert content.index("开端") < content.index("变化") < content.index("收尾")
    assert 480 <= width <= 1000


def test_caption_title_and_source_are_escaped_and_bounded():
    content, _ = comic._layout_html(
        [panel()] * 3,
        ["<script>bad()</script>&"] * 3,
        '<img onerror="bad()">',
        "群号1000000002 " + "原话题" * 40,
    )
    assert "&lt;script&gt;" in content and "&lt;img" in content
    assert "<script>bad()" not in content and "1000000002" not in content
    assert "…" in content


@pytest.mark.parametrize("images", [[], [b"bad"] * 3, [panel()] * 2, [panel(size=(20, 20))] * 3])
async def test_invalid_panels_never_start_the_browser(images, tmp_path, monkeypatch):
    renderer = AsyncMock()
    monkeypatch.setattr(comic.report_renderer, "render_to_file", renderer)
    with pytest.raises(ValueError):
        await comic.render_group_comic(
            images, ["一", "二", "三"], tmp_path, title="测试", source_note="话题"
        )
    assert not renderer.called


def test_both_square_and_landscape_images_use_a_compact_three_panel_page():
    square = panel(size=(640, 640))
    _, square_width = comic._layout_html([square] * 3, ["一", "二", "三"], "标题", "来源")
    _, landscape_width = comic._layout_html([panel()] * 3, ["一", "二", "三"], "标题", "来源")
    assert square_width == landscape_width == 1000


@pytest.mark.skipif(find_report_browser() is None, reason="需要本机Edge或Chromium")
async def test_actual_browser_preserves_all_three_panel_colors_and_bounds(tmp_path):
    pytest.importorskip("playwright.async_api")
    payload = await comic.render_group_comic(
        [panel(color) for color in ("teal", "coral", "gold")],
        ["起：同一话题的开端", "承：事情发生变化", "转：一句收尾"],
        tmp_path,
        title="三格剧情排版测试",
        source_note="合成图仅验证布局，不代表漫画成品",
    )
    (tmp_path / "three-panels.png").write_bytes(payload)
    with Image.open(io.BytesIO(payload)) as picture:
        assert picture.format == "PNG" and picture.size == (1000, 620)
        assert picture.getpixel((175, 250))[:3] == (0, 128, 128)
        assert picture.getpixel((500, 250))[:3] == (255, 127, 80)
        assert picture.getpixel((825, 250))[:3] == (255, 215, 0)
