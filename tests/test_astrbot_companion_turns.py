"""Offline conversations through the QQ plugin entry, without contacting any account."""

from __future__ import annotations

import base64
import io
import json
import sys
import types
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image as Raster
from test_astrbot_group_chat import Event
from test_astrbot_plugin import _load_plugin

from app import astrbot_compare
from app.astrbot_companion_turns import CompanionTurns


class Plain:
    def __init__(self, text):
        self.text = text


class Image:
    type = "image"

    def __init__(self, ref):
        self.url = self.file = self.path = ref

    @classmethod
    def fromBytes(cls, payload):
        return cls(payload)


class Reply:
    type = "reply"

    def __init__(self, id, sender_id=None, chain=()):
        self.id, self.sender_id, self.chain = id, sender_id, chain


class Record:
    type = "record"

    def __init__(self, file, text=None):
        self.file, self.text = file, text


class Room:
    def __init__(self, plugin):
        self.plugin = plugin
        self.serial = 120
        self.clock = 700.0
        self.api = AsyncMock()
        self.api.call_action.return_value = {"messages": []}

    async def post(self, text, *, user="8", group="1000000001", at=None, images=(), reply=None, record=None, mid=None):
        self.serial += 1
        segments = ([{"type": "at", "data": {"qq": at}}] if at else [])
        segments += [{"type": "text", "data": {"text": text}}] if text else []
        segments += [{"type": "image", "data": {"url": url}} for url in images]
        if record:
            segments += [{"type": "record", "data": {"file": record.file}}]
        chain = ([reply] if reply else []) + [Image(ref) for ref in images] + ([record] if record else [])
        ev = Event(text, user=user, group=group or "", self_id="99", message_id=str(mid or self.serial),
                   segments=segments, components=chain)
        raw = ev.message_obj.raw_message
        if not group:
            raw.update(message_type="private")
            raw.pop("group_id", None)
        raw["time"] = int(datetime(2026, 11, 16, 10, tzinfo=UTC).timestamp()) + self.serial
        ev.bot = SimpleNamespace(api=self.api)
        ev.unified_msg_origin = f"qq:{group or 'private'}:{user}"
        ev.send = AsyncMock()
        ev.chain_result = lambda x: x
        self.plugin._get_group_router().prepare_event(ev)
        await self.plugin.route_native_group_message(ev)
        return ev.send.await_args.args[0] if ev.send.await_count else []


def make_room(monkeypatch):
    mod = _load_plugin(monkeypatch)
    components = types.ModuleType("astrbot.api.message_components")
    for c in (Image, Plain, Reply, Record):
        setattr(components, c.__name__, c)
    monkeypatch.setitem(sys.modules, components.__name__, components)
    context = SimpleNamespace(get_using_provider_async=AsyncMock(), get_provider_by_id=lambda _: None)
    p = mod.QQChatPlugin(context, {"legacy_chat": False, "group_chat_groups": ["1000000001", "1000000002"],
                                    "group_chat_addressed_only": True})
    p.runtime = SimpleNamespace(_core=None)
    p._legacy_event_allowed = AsyncMock(return_value=True)
    monkeypatch.setattr("app.astrbot_owner_memory.observe", AsyncMock())
    monkeypatch.setattr("app.astrbot_plugin_status.private_companion_photo_hint", lambda *args: "")
    monkeypatch.setattr("app.astrbot_weather.handle_command", AsyncMock(return_value=False))
    monkeypatch.setattr("app.astrbot_singing.handle_command", AsyncMock(return_value=False))
    monkeypatch.setattr("app.astrbot_card_lookup.handle", AsyncMock(return_value=False))
    room = Room(p)
    p._companion_turns = CompanionTurns(clock=lambda: room.clock)
    return room


def say(chain):
    return "\n".join(c.text for c in chain if isinstance(c, Plain))


@pytest.mark.asyncio
async def test_images_across_messages_keep_sender_scope_and_selected_order(room, monkeypatch):
    pair = AsyncMock(return_value=[Plain("两张看过了")])
    monkeypatch.setattr("app.astrbot_companion_turns.compare", pair)
    assert "两张" in say(await room.post("可以帮我对比一下两张图吗"))
    assert "另一张" in say(await room.post("", images=["a"]))
    assert not await room.post("", user="someone", images=["unrelated"])
    assert not await room.post("", group="1000000002", images=["another-room"])
    assert say(await room.post("", images=["b"])) == "两张看过了"
    assert pair.await_args.args[1] == ["a", "b"]
    assert "两张图" in pair.await_args.args[2]
    assert "第几张" in say(await room.post("圈出不同", images=["1", "2", "3"]))
    await room.post("第一张和第三张")
    assert pair.await_args.args[1] == ["1", "3"]
    await room.post("尾巴是不是低了", reply=Reply(3, sender_id=12, chain=[Image("reference")]), images=["new"])
    assert pair.await_args.args[1] == ["reference", "new"]
    assert not await room.post("你是不是也喜欢吃面？")
    room.clock += 500
    assert "两张" in say(await room.post("看看这两张图哪里不一样"))
    assert pair.await_count == 3


@pytest.mark.asyncio
async def test_offers_require_personal_confirmation_and_keep_ordinary_messages_free(room):
    assert "？" in say(await room.post("明天面试，有点紧张"))
    assert not await room.post("好啊", user="17")
    assert not await room.post("行", group="1000000002")
    assert say(await room.post("不用了"))
    assert not await room.post("好啊")
    assert not await room.post("我不想模拟面试")
    assert not await room.post("陪我模拟面试", at="someone-else")
    assert not await room.post("他说要陪我模拟面试")
    assert not await room.post("我刚回来", group=None)
    assert "群里" in say(await room.post("我刚回来了"))
    room.clock += 181
    assert not await room.post("可以")
    await room.post("别说话了", at="99")
    assert not await room.post("明天面试好紧张")


@pytest.mark.asyncio
async def test_rehearsal_turn_review_resume_and_exit_use_actual_speech(room, monkeypatch):
    model = room.plugin.context.get_using_provider_async.return_value
    model.text_chat.return_value = SimpleNamespace(completion_text="你为什么想加入我们？")
    assert "想练哪种" in say(await room.post("能陪我练练吗"))
    assert "加入" in say(await room.post("面试，你当面试官"))
    assert not await room.post("这是别人的事", user="18")
    assert not await room.post("顺路买杯咖啡", at="18")
    hear = AsyncMock(return_value=("我负责把账本从纸上搬到了电脑里", ""))
    monkeypatch.setattr("app.astrbot_companion_turns.hear", hear)
    voice = AsyncMock(side_effect=lambda p, e, t, spoken: [Record("own-voice", t)] if spoken else [Plain(t)])
    monkeypatch.setattr("app.astrbot_companion_turns.reply_chain", voice)
    assert isinstance((await room.post("", record=Record("qq-voice")))[0], Record)
    assert hear.await_count == 1
    await room.post("暂停，我刚才哪句话能说得好一点？")
    payload = json.loads(model.text_chat.await_args.kwargs["prompt"])
    assert payload["刚才实际说的原话"] == "我负责把账本从纸上搬到了电脑里"
    assert not await room.post("晚上吃面条")
    await room.post("继续练")
    assert "继续练" in model.text_chat.await_args.kwargs["prompt"]
    assert "先收" in say(await room.post("我不想练了"))
    assert not await room.post("继续")


@pytest.mark.asyncio
async def test_spoken_private_start_and_duplicate(room, monkeypatch):
    monkeypatch.setattr("app.astrbot_companion_turns.hear", AsyncMock(return_value=("你当面试官，陪我练练", "")))
    room.plugin.context.get_using_provider_async.return_value.text_chat.return_value = SimpleNamespace(completion_text="请介绍一下自己")
    monkeypatch.setattr("app.astrbot_companion_turns.reply_chain", AsyncMock(return_value=[Record("voice")]))
    assert isinstance((await room.post("", group=None, record=Record("input"), mid="555"))[0], Record)
    assert not await room.post("", group=None, record=Record("input"), mid="555")
    assert not await room.post("", user="7", record=Record("unaddressed"))


@pytest.mark.asyncio
async def test_group_quotes_latest_decision_and_checks_mentioned_person(room):
    await room.post("有人想八点出发吗？", user="7", mid=41)
    await room.post("定了，今晚九点南门集合", user="7", mid=42)
    await room.post("你能带两把伞吗", user="6", at="7", mid=43)
    await room.post("你确认一下车位", user="6", at="8", mid=44)
    await room.post("别的群的秘密", group="1000000002", mid=46)
    model = room.plugin.context.get_using_provider_async.return_value
    model.text_chat.return_value = SimpleNamespace(completion_text=json.dumps({"items": [
        {"kind": "decided", "id": "42", "quote": "今晚九点南门集合"},
        {"kind": "yours", "id": "43", "quote": "你能带两把伞吗"},
        {"kind": "yours", "id": "44", "quote": "你确认一下车位"},
        {"kind": "decided", "id": "41", "quote": "有人想八点出发吗？"},
        {"kind": "news", "id": "42", "quote": "十点见"}, {"kind": "news", "id": "999", "quote": "虚构的原话"},
    ]}, ensure_ascii=False))
    result = await room.post("所以今晚到底怎么安排？")
    assert isinstance(result[0], Reply) and result[0].id == "42"
    message = say(result)
    assert "已定" in message and "今晚九点南门集合" in message
    assert "在等你" in message and "你确认一下车位" in message
    assert "群里提到" in message and "十点" not in message and "虚构" not in message
    assert "之前有人提过" in message
    assert "别的群" not in model.text_chat.await_args.kwargs["prompt"]
    room.api.call_action.assert_awaited_once_with("get_group_msg_history", group_id=1000000001, count=80, reverseOrder=False)


@pytest.mark.asyncio
async def test_visible_images_only_and_real_annotation(room, monkeypatch):
    def tile(color):
        im = Raster.new("RGB", (120, 100), color)
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
    frames = {"a": tile("white"), "b": tile("navy")}
    monkeypatch.setattr(astrbot_compare, "_load_image", AsyncMock(side_effect=lambda ref: astrbot_compare._decode_image(frames[ref])))
    vision = SimpleNamespace(text_chat=AsyncMock(return_value=SimpleNamespace(completion_text=json.dumps({
        "summary": "B 的底色更深", "differences": [
            {"text": "底色变化", "a": [50, 50, 210, 290], "b": [40, 40, 300, 300]},
            {"text": "轮廓看不清", "a": [-1, 3, 2, 4], "b": None}]}))))
    room.plugin.context.get_provider_by_id = lambda _: vision
    chain = await room.post("这两张有什么区别", images=["a", "b"])
    assert "圈 1" in say(chain) and "圈 2" not in say(chain)
    preview = Raster.open(io.BytesIO(chain[1].file))
    assert preview.width > preview.height and preview.getpixel((20, 100)) != preview.getpixel((preview.width//2+20, 100))
    urls = vision.text_chat.await_args.kwargs["image_urls"]
    assert len(urls) == 1
    board = Raster.open(io.BytesIO(base64.b64decode(urls[0].split(",")[1])))
    assert board.width == preview.width and board.height == preview.height
    assert "一致" in say(await room.post("改了哪里", images=["a", "a"]))
    assert vision.text_chat.await_count == 1


def test_exact_foreground_move_is_grounded_in_pixels():
    a, b = (Raster.new("RGB", (280, 220), "white") for _ in range(2))
    a.paste("navy", (90, 50, 161, 106))
    b.paste("navy", (90, 115, 161, 171))
    description, boxes = astrbot_compare._translation([a, b])
    assert "向下" in description and "29.5%" in description and boxes[0][1] < boxes[1][1]
    b.paste("red", (100, 125, 112, 137))
    assert astrbot_compare._translation([a, b]) is None
    b.paste("red", (90, 115, 161, 171))
    description, _ = astrbot_compare._translation([a, b])
    assert "左右位置一致" in description and "深蓝色变为红色" in description
    jpeg_frames = []
    for im in (a, b):
        stream = io.BytesIO()
        im.save(stream, "JPEG", quality=92)
        jpeg_frames.append(astrbot_compare._decode_image(stream.getvalue())[0])
    assert "左右位置一致" in astrbot_compare._translation(jpeg_frames)[0]
