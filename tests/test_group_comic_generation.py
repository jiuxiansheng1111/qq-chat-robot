from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from app.services.group_comic_generation import (
    ComicGenerationError,
    free_comic_provider,
    generate_illustrated_comic,
)

FREE = {
    "api_url": "https://open.bigmodel.cn/api/paas/v4/images/generations",
    "api_key": "not-a-real-key", "model": "cogview-3-flash", "api_protocol": "images",
}
TOPICS = [{"topic": "电子维修", "detail": "大家讨论电阻和LED灯珠的焊接与区分。"}]


def config(providers=None):
    return SimpleNamespace(get_drawing_provider_configs=lambda: providers or [dict(FREE)])


@pytest.mark.parametrize(("key", "value"), [
    ("model", "cogview-4"), ("model", "gpt-image-2"),
    ("api_url", "https://example.invalid/api/paas/v4/images/generations"),
    ("api_url", "http://open.bigmodel.cn/api/paas/v4/images/generations"),
    ("api_url", "https://open.bigmodel.cn/api/paas/v4/images/generations?redirect=1"),
    ("api_url", "https://other@open.bigmodel.cn/api/paas/v4/images/generations"),
    ("api_key", ""), ("api_protocol", "chat"),
])
def test_paid_or_unrecognized_endpoint_is_rejected(key, value):
    with pytest.raises(ComicGenerationError):
        free_comic_provider(config([{**FREE, key: value}]))


class FakeDrawing:
    calls: ClassVar[list] = []
    outputs: ClassVar[list] = []

    def __init__(self, config):
        self.config = config

    async def generate_image(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs, self.config.get_drawing_provider_configs(),
                           self.config.get_drawing_network_retries()))
        return self.outputs.pop(0)


@pytest.fixture(autouse=True)
def reset_calls(monkeypatch):
    FakeDrawing.calls = []
    FakeDrawing.outputs = [(b"first", None), (b"second", None), (b"third", None)]
    monkeypatch.setattr("app.services.group_comic_check.verify_group_comic_panels",
                        AsyncMock(return_value={"passed": True, "reason": "画面检查通过"}))


async def test_one_topic_three_ordered_requests_no_paid_fallback_then_local_layout(tmp_path, monkeypatch):
    render = AsyncMock(return_value=b"comic-page")
    monkeypatch.setattr("app.services.group_comic_layout.render_group_comic", render)
    cfg = config([dict(FREE), {**FREE, "model": "paid-other"}])
    image, plan = await generate_illustrated_comic(TOPICS, cfg, FakeDrawing(cfg), tmp_path)
    assert image == b"comic-page" and len(FakeDrawing.calls) == 3
    assert len(plan["selected_topics"]) == 1 and len(plan["panel_captions"]) == 3
    assert len(set(plan["panel_image_sha256"])) == 3
    assert plan["render_mode"] == "illustrated_story"
    for _, kwargs, providers, retries in FakeDrawing.calls:
        assert kwargs == {"images_data": None, "disable_retry": True}
        assert len(providers) == 1 and providers[0]["model"] == "cogview-3-flash" and retries == 0
    assert render.await_args.args[0] == [b"first", b"second", b"third"]


async def test_one_missing_or_duplicate_panel_never_becomes_a_generic_card(tmp_path, monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr("app.services.group_comic_layout.render_group_comic", render)
    cfg = config()
    FakeDrawing.outputs = [(b"first", None), (None, "upstream unavailable")]
    with pytest.raises(ComicGenerationError, match="第2格"):
        await generate_illustrated_comic(TOPICS, cfg, FakeDrawing(cfg), tmp_path)
    assert len(FakeDrawing.calls) == 2 and not render.called
    FakeDrawing.calls = []
    FakeDrawing.outputs = [(b"same", None), (b"same", None)]
    with pytest.raises(ComicGenerationError, match="重复"):
        await generate_illustrated_comic(TOPICS, cfg, FakeDrawing(cfg), tmp_path)
    assert len(FakeDrawing.calls) == 2 and not render.called


async def test_no_specific_material_does_not_call_the_drawing_api(tmp_path):
    cfg = config()
    with pytest.raises(ComicGenerationError, match="素材"):
        await generate_illustrated_comic(
            [{"topic": "群聊", "detail": "大家聊了一些事。"}], cfg, FakeDrawing(cfg), tmp_path,
        )
    assert not FakeDrawing.calls


async def test_unrelated_photos_or_unavailable_checker_never_get_delivered(tmp_path, monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr("app.services.group_comic_layout.render_group_comic", render)
    monkeypatch.setattr("app.services.group_comic_check.verify_group_comic_panels",
                        AsyncMock(return_value={"passed": False, "reason": "画面与漫画要求不符"}))
    cfg = config()
    with pytest.raises(ComicGenerationError, match="内容检查"):
        await generate_illustrated_comic(TOPICS, cfg, FakeDrawing(cfg), tmp_path)
    assert len(FakeDrawing.calls) == 3 and not render.called
