from __future__ import annotations

import asyncio
import io
import socket
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from app.services.local_report_render import (
    LocalReportRenderer,
    LocalReportRenderError,
    _public_asset_url,
    _RenderOptions,
    _resolve_static_images,
    _screenshot_scale,
    _trusted_template_image,
    find_report_browser,
)


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Windows/win.ini",
        "http://127.0.0.1/avatar.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.2/avatar.png",
        "http://[::1]/avatar.png",
        "http://localhost/avatar.png",
        "https://machine.local/avatar.png",
        "https://user:password@cdn.example/avatar.png",
        "https://cdn.example:8443/avatar.png",
        "javascript:alert(1)",
    ],
)
def test_private_and_nonstatic_asset_addresses_are_rejected(url):
    assert _public_asset_url(url) is None


async def test_asset_dns_is_checked_and_pinned_before_browser_requests(monkeypatch):
    async def lookup(host, *_args, **_kwargs):
        addresses = ["192.168.0.1"] if host == "internal.example" else ["93.184.216.34"]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in addresses]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", lookup)
    urls, rules = await _resolve_static_images(
        {"https://internal.example/avatar.png", "https://cdn.example/chart.png"}
    )

    assert urls == {"https://cdn.example/chart.png"}
    assert rules == ["MAP cdn.example 93.184.216.34"]


async def test_proxy_prefetch_only_accepts_official_static_images(monkeypatch):
    from app.services import http_routing

    image = io.BytesIO()
    Image.new("RGB", (10, 10), "green").save(image, format="PNG")
    requested = []
    prefix = (
        "https://cdn.jsdmirror.com/gh/SXP-Simon/astrbot_plugin_qq_group_daily_analysis@main/assets/"
    )
    good = prefix + "HatsuneMiku/test.png"
    invalid_payload = prefix + "HatsuneMiku/error.png"

    def respond(request):
        requested.append(str(request.url))
        return httpx.Response(
            200, content=image.getvalue() if str(request.url) == good else b"<script>bad()</script>"
        )

    monkeypatch.setattr(
        http_routing,
        "outbound_http_client",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs),
    )
    renderer = LocalReportRenderer()
    await renderer._cache_template_images(
        {good, invalid_payload, "https://arbitrary.example/image.png", good + "?secret=test"}
    )

    assert set(requested) == {good, invalid_payload}
    assert set(renderer._static_images) == {good}
    assert renderer._static_images[good][0] == "image/png"
    assert not _trusted_template_image(prefix + "../secret.png")
    assert not _trusted_template_image(good + "?secret=test")


def test_viewport_and_pixel_budgets_preserve_full_report_before_density():
    options = _RenderOptions.parse(
        {"viewport_width": 1440, "type": "png", "device_scale_factor_level": "ultra"},
        {"width": 940},
    )
    assert options.width == 940
    assert options.scale == 2
    assert _screenshot_scale(940, 8000, options.scale) == "device"
    assert _screenshot_scale(940, 15000, options.scale) == "css"
    with pytest.raises(LocalReportRenderError):
        _screenshot_scale(940, 31_000, options.scale)
    with pytest.raises(LocalReportRenderError):
        _RenderOptions.parse({"width": 99_999}, {})


async def test_oversized_html_is_rejected_before_browser_start():
    renderer = LocalReportRenderer(browser_path="not-started")
    with pytest.raises(LocalReportRenderError):
        await renderer.render("中" * 5_000_000)


@pytest.mark.skipif(find_report_browser() is None, reason="需要已安装 Edge 或 Chromium")
async def test_browser_keeps_inline_layout_and_blocks_private_network(tmp_path):
    pytest.importorskip("playwright.async_api")
    connections = []

    async def incoming(reader, writer):
        connections.append(True)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(incoming, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    content = f"""<!doctype html><html><head>
        <meta charset="utf-8"><meta name="viewport" content="width=420">
        <style>body {{margin:0;background:#f0fafa;font:22px 'Noto Sans SC';height:240px}}
        h1 {{color:#2a9d95;margin:0}} svg {{width:360px;height:100px}}</style>
        <script src="http://127.0.0.1:{port}/external.js"></script>
        </head><body><h1>初音未来 · 群聊日报</h1>
        <p>今日话题、群友称号与金句</p><svg viewBox="0 0 360 100">
        <rect x="20" y="10" width="50" height="90" fill="#39c5bb"/>
        <rect x="100" y="40" width="50" height="60" fill="#f8bbd0"/></svg>
        <img src="http://127.0.0.1:{port}/private.png">
        <iframe src="http://127.0.0.1:{port}/frame"></iframe>
        <script>document.body.style.height='460px';
        fetch('http://127.0.0.1:{port}/secret').catch(()=>{{}});
        new WebSocket('ws://127.0.0.1:{port}/socket');</script>
        </body></html>"""
    try:
        async with server:
            image_bytes = await LocalReportRenderer().render(
                content, {"type": "png", "viewport_height": 220}
            )
        assert connections == []
        with Image.open(io.BytesIO(image_bytes)) as image:
            assert image.format == "PNG"
            assert image.size == (420, 460)
            assert image.getpixel((25, 200))[:3] != (255, 255, 255)
        (tmp_path / "chinese-report.png").write_bytes(image_bytes)
    finally:
        server.close()
        await server.wait_closed()


async def test_file_cache_cleanup_keeps_unrelated_user_files(tmp_path, monkeypatch):
    renderer = LocalReportRenderer()
    monkeypatch.setattr(renderer, "render", AsyncMock(return_value=b"\x89PNG\r\n\x1a\nimage"))
    for index in range(36):
        (tmp_path / f"local-report-{index}.png").write_bytes(b"old")
    unrelated = tmp_path / "keep-my-report.png"
    unrelated.write_bytes(b"keep")

    result = await renderer.render_to_file("<html>日报</html>", {}, tmp_path)

    assert result.endswith(".png")
    assert len(list(tmp_path.glob("local-report-*"))) == 32
    assert unrelated.read_bytes() == b"keep"
