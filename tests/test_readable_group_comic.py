from __future__ import annotations

import io
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.services import readable_group_comic as comic
from app.services.local_report_render import find_report_browser

TOPICS = [
    {"topic": "抽卡爆率讨论", "detail": "群友比较不同账号的抽卡结果，讨论随机运气与爆率。"},
    {"topic": "语音翻唱服务", "detail": "群友请求助手翻唱歌曲，并讨论音色与翻唱效果。"},
    {
        "topic": "炉石卡组讨论",
        "detail": "机器人推荐中混进了狂野牌，群友指出需要区分标准模式与狂野模式。",
    },
]


def test_panel_order_limit_and_identity_redaction():
    topics = [
        {"topic": "群号1000000002的话题", "detail": "QQ号：12345 和 [849855989] 讨论抽卡。"},
        *TOPICS,
    ]
    content, height = comic._comic_html(topics, "群1000000002的漫画")
    assert content.count('<section class="panel">') == 3
    assert "炉石卡组讨论" not in content
    assert content.index("抽卡爆率讨论") < content.index("语音翻唱服务")
    assert all(identity not in content for identity in ("1000000002", "12345", "849855989"))
    assert comic._DISCLAIMER in content
    assert height <= 1600


def test_html_is_escaped_and_has_no_external_resources_or_scripts():
    content, _ = comic._comic_html(
        [
            {
                "topic": '<script>alert("topic")</script>',
                "detail": '<img src="https://evil.example/x" onerror="bad()">& 原始内容',
            },
        ],
        '<svg onload="bad()">',
    )
    assert "&lt;script&gt;" in content
    assert "&lt;img" in content
    assert "evil.example" not in content
    assert "<svg onload=" not in content
    assert all(
        token not in content.lower()
        for token in ("<script", "<img", "<iframe", "<link", "@import", "url(")
    )


def test_long_topics_are_bounded_and_not_rewritten_as_a_story():
    topic = "特别长的话题" * 50
    detail = "这句文字来自话题原文。" * 50
    content, height = comic._comic_html([{"topic": topic, "detail": detail}] * 3, "长标题" * 50)
    assert topic not in content and detail not in content
    assert comic._excerpt(topic, 38) in content
    assert comic._excerpt(detail, 88) in content
    assert "…" in content
    assert "话题原文节选" in content
    assert "确实发生" not in content
    assert 0 < height <= 1600


def test_domain_dataclasses_are_compatible_and_unknown_topics_use_dialogue():
    @dataclass
    class Topic:
        topic: str
        detail: str

    content, _ = comic._comic_html([Topic("日常交流", "群友分享周末的阅读感受。")], "群聊三格漫画")
    assert "日常交流" in content and "周末的阅读感受" in content
    assert "交流话题" in content
    assert "人物为通用示意" in content


@pytest.mark.parametrize("topics", [[], None, "不是真实话题列表"])
async def test_empty_input_never_starts_a_browser(topics, tmp_path, monkeypatch):
    renderer = AsyncMock()
    monkeypatch.setattr(comic.report_renderer, "render_to_file", renderer)
    with pytest.raises(ValueError):
        await comic.render_readable_comic(topics, tmp_path)
    assert not renderer.called


async def test_render_wrapper_uses_existing_renderer_and_returns_png_bytes(tmp_path, monkeypatch):
    png = tmp_path / "local-report-comic.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\ncomic")
    render = AsyncMock(return_value=str(png))
    monkeypatch.setattr(comic.report_renderer, "render_to_file", render)
    result = await comic.render_readable_comic(TOPICS, tmp_path)
    assert result == png.read_bytes()
    content, options, directory = render.await_args.args
    assert options["type"] == "png" and options["device_scale_factor"] == 1
    assert options["viewport_width"] == 960 and options["viewport_height"] <= 1600
    assert options["full_page"] is False and directory == tmp_path
    assert "<script" not in content


@pytest.mark.skipif(find_report_browser() is None, reason="需要本机已安装 Edge 或 Chromium")
async def test_real_local_browser_renders_readable_three_panels(tmp_path):
    pytest.importorskip("playwright.async_api")
    payload = await comic.render_readable_comic(TOPICS, tmp_path)
    destination = tmp_path / "comic.png"
    destination.write_bytes(payload)
    with Image.open(io.BytesIO(payload)) as picture:
        assert picture.format == "PNG"
        assert picture.width == 960 and 1200 <= picture.height <= 1600
        assert picture.getpixel((30, 30))[:3] != (255, 255, 255)
