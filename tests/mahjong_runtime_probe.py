"""Run with the installed AstrBot Python; real scheduler and event, synthetic transport only."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from astrbot.api.message_components import Image, Plain
from astrbot.core.pipeline.context_utils import call_handler
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from PIL import Image as PillowImage

import main as plugin_main
from app import astrbot_mahjong as adapter
from app.services.mahjong.cards import ReportMessage


class Event(AstrMessageEvent):
    def __init__(self, kind):
        message = AstrBotMessage()
        message.type = MessageType.FRIEND_MESSAGE if kind == "private" else MessageType.GROUP_MESSAGE
        message.self_id = "90001"
        message.sender = SimpleNamespace(user_id="90002", nickname="synthetic")
        message.message = []
        message.raw_message = {"message_type": kind, "self_id": "90001", "user_id": "90002",
                               "group_id": "90003" if kind == "group" else "", "sender": {}}
        super().__init__("雀魂分析 链接=synthetic", message, PlatformMetadata("aiocqhttp", "test", "test"), "90002")
        self.sent = []

    async def send(self, message):
        # No QQ account or network transport is reachable in this probe.
        self.sent.append(message)
        self._has_send_oper = True


class HandlerStage:
    def __init__(self, handler):
        self.handler = handler

    async def process(self, event):
        async for value in call_handler(event, self.handler):
            yield value


class ReplyStage:
    async def process(self, event):
        result = event.get_result()
        if result and result.chain:
            await event.send(result)


async def run(handler, event):
    scheduler = object.__new__(PipelineScheduler)
    scheduler.stages = [HandlerStage(handler), ReplyStage()]
    await scheduler._process_stages(event)


async def check():
    reached = False

    async def old_handler(event):
        nonlocal reached
        event.stop_event()
        yield event.make_result().message("pending")
        reached = True

    await run(old_handler, Event("private"))
    assert not reached, "Expected installed scheduler to reproduce the premature exit"

    plugin = plugin_main.QQChatPlugin(SimpleNamespace(), {"legacy_chat": False})
    plugin.runtime = SimpleNamespace(_core=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))
    plugin._legacy_event_allowed = AsyncMock(return_value=True)
    review = AsyncMock(return_value="finished")
    pages = (ReportMessage("summary"), ReportMessage("page-one", ROOT / "synthetic-one.jpg"),
             ReportMessage("page-two", ROOT / "synthetic-two.jpg"), ReportMessage("finished"))
    with patch.object(adapter, "handle_native_review", review), patch(
        "app.services.mahjong.cards.report_messages", AsyncMock(return_value=pages)
    ):
        for kind in ("private", "group"):
            event = Event(kind)
            await run(plugin.on_mahjong_review, event)
            assert len(event.sent) == 5
            assert sum(isinstance(c, Image) for msg in event.sent for c in msg.chain) == 2
            assert isinstance(event.sent[-1].chain[0], Plain) and event.sent[-1].chain[0].text == "finished"
            assert event.is_stopped() and event.call_llm is False
    assert review.await_count == 2
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "card.jpg"
        PillowImage.new("RGB", (20, 20), "white").save(path, "JPEG")
        chain = Event("private").chain_result([Plain("card"), Image.fromFileSystem(str(path))])
        bot = SimpleNamespace(send_private_msg=AsyncMock(), send_group_msg=AsyncMock())
        await AiocqhttpMessageEvent.send_message(bot, chain, is_group=False, session_id="90002")
        await AiocqhttpMessageEvent.send_message(bot, chain, is_group=True, session_id="90003")
        for send in (bot.send_private_msg, bot.send_group_msg):
            send.assert_awaited_once()
            assert send.call_args.kwargs["message"][1]["data"]["file"].startswith("base64:///9j/")
    print(json.dumps({"real_scheduler_regression_reproduced": True, "private_and_group_completed": True,
                      "images_per_report": 2, "onebot_image_serialization": True, "real_qq_messages_sent": 0}))


asyncio.run(check())
