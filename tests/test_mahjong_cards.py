"""Real local rendering, delivery order, and failure fallback without sending QQ messages."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.plugins.mahjong import ReportStore
from app.services.mahjong import cards
from app.services.mahjong.highlights import select_decisions
from app.services.mahjong.records import parse_record
from app.services.mahjong.review import review, summary


@pytest.fixture(scope="module")
def reviewed():
    fixture = Path(__file__).parent / "fixtures/mahjong/upstream/rinshan.json"
    return review(parse_record(fixture.read_bytes(), match="半庄"), 3)


def test_real_cards_cover_every_selected_node_and_fit_qq(reviewed, tmp_path):
    paths = cards.render_cards(reviewed, "card-test", tmp_path)
    selected = select_decisions(reviewed.analysis.decisions)
    assert len(paths) == len(selected)
    for path, (node, _) in zip(paths, selected, strict=True):
        assert path.name.endswith(f"node-{node}.jpg")
        assert path.resolve().is_relative_to(tmp_path.resolve())
        assert path.stat().st_size < 2 * 1024 * 1024
        with Image.open(path) as image:
            assert image.format == "JPEG" and image.mode == "RGB"
            assert image.width == 1200 and 700 < image.height < 6000
            assert not image.info.get("progressive")
            image.verify()


def test_wrapping_never_drops_text_or_exceeds_width():
    font = ImageFont.truetype(str(cards._find_font_path()), 28)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text = "当时已立直；建议保留退路，不能把未见数当实际山余。" * 40
    lines = cards.wrap_text(draw, text, font, 550)
    assert "".join(lines) == text
    assert all(draw.textlength(line, font=font) <= 550 for line in lines)


async def test_delivery_keeps_all_pages_and_owner_scope(reviewed, tmp_path, monkeypatch):
    store = ReportStore()
    owner = ("bot", "group", "human")
    job = store.put(owner, reviewed)
    text = summary(reviewed, job)
    state = SimpleNamespace(mahjong_reports=store)
    calls = []
    paths = tuple(tmp_path / f"{i}.jpg" for i in range(4))

    def render(*args):
        calls.append(args)
        return paths

    monkeypatch.setattr(cards, "render_cards", render)
    messages = await cards.report_messages(state, text, owner, root=tmp_path)
    assert [m.image for m in messages if m.image] == list(paths)
    assert "决策" in messages[0].text and "导出" in messages[-1].text
    assert len(calls) == 1
    denied = await cards.report_messages(state, text, ("bot", "group", "other"), root=tmp_path)
    assert not any(m.image for m in denied) and len(calls) == 1


async def test_render_failure_preserves_whole_text(reviewed, monkeypatch):
    store = ReportStore()
    owner = ("bot", "", "human")
    text = summary(reviewed, store.put(owner, reviewed))

    def fail(*args):
        raise RuntimeError("font unavailable")

    monkeypatch.setattr(cards, "render_cards", fail)
    messages = await cards.report_messages(SimpleNamespace(mahjong_reports=store), text, owner)
    assert all(m.image is None for m in messages)
    assert "".join(m.text for m in messages) == text


async def test_native_handler_emits_all_images_with_captions(monkeypatch, tmp_path):
    from test_astrbot_plugin import FakeEvent, _load_plugin

    module = _load_plugin(monkeypatch)
    plugin = module.QQChatPlugin(SimpleNamespace(), {"legacy_chat": False})
    plugin.runtime = SimpleNamespace(_core=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))
    image_api = ModuleType("astrbot.api.message_components")
    image_api.Image = SimpleNamespace(fromFileSystem=lambda path: ("image", path))
    image_api.Plain = lambda value: ("text", value)
    monkeypatch.setitem(sys.modules, image_api.__name__, image_api)

    async def fake_review(*args):
        return "report"

    async def fake_messages(state, text, owner):
        assert owner == ("bot", "", "human")
        return (cards.ReportMessage("摘要"), cards.ReportMessage("第一页", tmp_path / "1.jpg"),
                cards.ReportMessage("第二页", tmp_path / "2.jpg"), cards.ReportMessage("导出说明"))

    monkeypatch.setattr("app.astrbot_mahjong.handle_native_review", fake_review)
    monkeypatch.setattr(cards, "report_messages", fake_messages)
    event = FakeEvent({"message_type": "private", "self_id": "bot", "user_id": "human", "sender": {}})
    event.get_platform_name = lambda: "aiocqhttp"
    event.get_message_str = lambda: "雀魂分析 帮助"
    event.get_extra = lambda *args: ""
    event.make_result = lambda: SimpleNamespace(message=lambda text: text)
    event.chain_result = lambda chain: chain
    event.send = AsyncMock()
    await plugin.on_mahjong_review(event)
    replies = [call.args[0] for call in event.send.call_args_list]
    assert replies == ["摘要", [("text", "第一页"), ("image", str(tmp_path / "1.jpg"))],
                       [("text", "第二页"), ("image", str(tmp_path / "2.jpg"))], "导出说明"]
