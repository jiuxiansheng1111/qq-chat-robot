"""随机二次元图片别名的隔离测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.astrbot_plugin_images import (
    IMAGE_PLUGIN_NAME,
    forward_anime_image,
    is_anime_image_request,
)


@pytest.mark.parametrize(
    "text",
    [
        "随机二次元图片",
        "来张二次元图",
        "随机动漫图",
        "来张随机二次元图片",
        "帮我来一张随机动漫图",
        "给我发一张二次元插画吧",
        "给我随机一张动漫图",
        "  随机动漫图！  ",
    ],
)
def test_matches_short_image_requests(text: str) -> None:
    assert is_anime_image_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "随机二次元角色",
        "今日二次元角色",
        "我的二次元角色",
        "角色图鉴",
        "随机奥特曼角色",
        "给我来一张随机动漫图是什么意思",
        "为什么要用随机二次元图片",
        "不要随机二次元图片",
        "随机动漫图，顺便说说角色",
        "二次元图片",
        "动漫图",
        "关于随机动漫图",
        "",
    ],
)
def test_does_not_capture_role_catalog_or_discussion(text: str) -> None:
    assert not is_anime_image_request(text)


class FakeEvent:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_message_str(self) -> str:
        return self.text


class FakeImagePlugin:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []

    async def cmd_p(self, event: Any, query: str = ""):
        self.calls.append((event, query))
        yield "image-result"


@pytest.mark.asyncio
async def test_forwards_alias_to_loaded_plugin_random_command() -> None:
    event = FakeEvent("来张随机二次元图片")
    plugin = FakeImagePlugin()
    context = SimpleNamespace(
        get_registered_star=lambda name: SimpleNamespace(
            name=name, activated=True, star_cls=plugin
        )
    )

    iterator = await forward_anime_image(event, context)

    assert iterator is not None
    assert [item async for item in iterator] == ["image-result"]
    assert plugin.calls == [(event, "")]
    assert context.get_registered_star(IMAGE_PLUGIN_NAME).star_cls is plugin


@pytest.mark.asyncio
async def test_unmatched_text_does_not_query_plugin_registry() -> None:
    event = FakeEvent("随机二次元角色")

    def unexpected_lookup(_name: str) -> None:
        pytest.fail("未匹配的请求不应查询图片插件")

    result = await forward_anime_image(
        event, SimpleNamespace(get_registered_star=unexpected_lookup)
    )

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registered",
    [
        None,
        SimpleNamespace(activated=True, star_cls=None),
        SimpleNamespace(activated=False, star_cls=FakeImagePlugin()),
    ],
)
async def test_returns_none_when_plugin_is_missing_or_disabled(registered: Any) -> None:
    event = FakeEvent("随机二次元图片")
    context = SimpleNamespace(get_registered_star=lambda _name: registered)

    assert await forward_anime_image(event, context) is None


@pytest.mark.asyncio
async def test_returns_none_when_registry_lookup_fails_or_command_is_missing() -> None:
    event = FakeEvent("随机动漫图")

    def failed_lookup(_name: str) -> None:
        raise RuntimeError("plugin is not registered")

    assert await forward_anime_image(
        event, SimpleNamespace(get_registered_star=failed_lookup)
    ) is None
    assert await forward_anime_image(
        event,
        SimpleNamespace(get_registered_star=lambda _name: SimpleNamespace()),
    ) is None
