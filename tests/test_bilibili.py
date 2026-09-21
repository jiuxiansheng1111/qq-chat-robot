from types import SimpleNamespace

import httpx
import pytest

import app.services.bilibili as bilibili_module
from app.config import Settings
from app.services.bilibili import (
    BilibiliVideo,
    bilibili_card_content,
    choose_bilibili_video,
    format_play_count,
    parse_bilibili_video,
    search_bilibili_videos,
)


def video(
    *,
    bvid: str,
    title: str,
    play: int,
    description: str = "",
    tags: str = "",
) -> BilibiliVideo:
    return BilibiliVideo(
        bvid=bvid,
        title=title,
        author="测试UP",
        description=description,
        tags=tags,
        cover_url="https://i0.hdslb.com/test.jpg",
        play=play,
        duration="03:21",
        pubdate=1,
    )


def test_parse_bilibili_video_strips_search_highlight_and_normalizes_cover():
    item = {
        "bvid": "BV1xx411c7mD",
        "title": "<em class=\"keyword\">迪迦</em> 最终圣战",
        "author": "UP主",
        "description": "闪耀迪迦片段",
        "tag": "迪迦,奥特曼",
        "pic": "//i0.hdslb.com/bfs/archive/test.jpg",
        "play": "12.5万",
        "duration": "05:00",
        "pubdate": 123,
    }
    result = parse_bilibili_video(item)
    assert result is not None
    assert result.title == "迪迦 最终圣战"
    assert result.cover_url.startswith("https://")
    assert result.play == 125000


def test_choose_video_keeps_relevance_ahead_of_unrelated_view_count():
    relevant = video(
        bvid="BV1aa411c7mD",
        title="迪迦奥特曼 最终圣战 闪耀迪迦",
        play=2_000_000,
    )
    noisy = video(
        bvid="BV1bb411c7mD",
        title="千万播放热门混剪",
        description="标签里顺便提到迪迦奥特曼最终圣战",
        play=90_000_000,
    )
    assert choose_bilibili_video("迪迦奥特曼 最终圣战", [noisy, relevant]) == relevant


def test_choose_video_prefers_higher_views_inside_same_relevance_band():
    low = video(
        bvid="BV1cc411c7mD",
        title="猫和老鼠 搞笑合集 上",
        play=100_000,
    )
    high = video(
        bvid="BV1dd411c7mD",
        title="猫和老鼠 搞笑合集 下",
        play=8_000_000,
    )
    assert choose_bilibili_video("猫和老鼠 搞笑合集", [low, high]) == high


def test_card_content_is_short_and_informative():
    item = video(
        bvid="BV1ee411c7mD",
        title="测试",
        play=12_345_678,
    )
    content = bilibili_card_content(item)
    assert "UP：测试UP" in content
    assert "播放：" in content
    assert "时长：03:21" in content
    assert format_play_count(12_345_678).endswith("万")


@pytest.mark.asyncio
async def test_search_combines_totalrank_and_click_results(monkeypatch):
    async def handler(request: httpx.Request):
        if request.url.host == "www.bilibili.com":
            return httpx.Response(200, text="<html></html>")
        order = request.url.params.get("order")
        if order == "totalrank":
            result = [
                {
                    "bvid": "BV1ff411c7mD",
                    "title": "<em>迪迦</em> 最终圣战",
                    "author": "A",
                    "description": "",
                    "tag": "迪迦",
                    "pic": "//i0.hdslb.com/a.jpg",
                    "play": 100,
                    "duration": "01:00",
                }
            ]
        else:
            result = [
                {
                    "bvid": "BV1gg411c7mD",
                    "title": "迪迦 最终圣战 高燃",
                    "author": "B",
                    "description": "",
                    "tag": "迪迦",
                    "pic": "//i0.hdslb.com/b.jpg",
                    "play": 999999,
                    "duration": "02:00",
                }
            ]
        return httpx.Response(
            200,
            json={"code": 0, "message": "0", "data": {"result": result}},
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mocked_client(**kwargs):
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(bilibili_module.httpx, "AsyncClient", mocked_client)
    settings = Settings(_env_file=None)
    results = await search_bilibili_videos("迪迦 最终圣战", settings)

    assert {item.bvid for item in results} == {"BV1ff411c7mD", "BV1gg411c7mD"}
    assert choose_bilibili_video("迪迦 最终圣战", results).bvid == "BV1gg411c7mD"
