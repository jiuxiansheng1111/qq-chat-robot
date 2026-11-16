"""Collect one speaker's explicitly announced, multi-message turn in arrival order."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.astrbot_compare import image_refs
from app.astrbot_identity import QQ_PERSONA_PATH
from app.astrbot_rehearsal import hear
from app.astrbot_turn_features import observe_image
from app.services.chat_punctuation import natural_chat_text

_LOG = logging.getLogger(__name__)
_START = re.compile(r"^(?:(?:你)?(?:先)?等(?:一下|下|一会儿)?[，, ]*)?(?:先听我说完|听我说完|等我(?:说|发|讲)完|"
                    r"我(?:要|想|会)?分(?:几|多|两|[2-9])(?:条|段|次)(?:消息)?(?:发|说|讲)(?:给你)?|我还没(?:说|发|讲)完)"
                    r"(?:再(?:回|说|答)(?:复)?|再回答我)?(?:[哦哈啊吧])?(?=$|[，,。！!：:；;\s])")
_FINISH = re.compile(r"^(?:我(?:都|已经)?|都)?(?:发完了|说完了|讲完了|好了|你可以(?:回|说|回答)了|轮到你了|你怎么看)"
                     r"(?:[，,。！!：:；;\s]+(.*)|[？?]?)$")
_CANCEL = re.compile(r"(?:算了[，, ]*)?(?:别等了|不用等了|取消(?:等我说完|这段)?|这段(?:先)?算了|这段不用回了)[吧啦。！!]*")
_IDLE = 15 * 60


@dataclass
class Piece:
    text: str
    images: list
    audio: list


@dataclass
class Batch:
    expires: float
    pieces: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ListenFirst:
    title = "等你说完再一起看"

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.open = {}

    def interested(self, key, text, addressed):
        now = self.clock()
        for old in [k for k, v in self.open.items() if v.expires < now and not v.lock.locked()]:
            del self.open[old]
        return key in self.open or bool(_START.match(text))

    async def handle(self, host, event, key, text):
        begin = _START.match(text)
        batch = self.open.get(key)
        if not batch and not begin:
            return False
        if batch is None:
            if len(self.open) >= 128:
                await event.send(event.plain_result("我这会儿接不过来这么多段，稍后再叫我听吧"))
                return True
            batch = self.open[key] = Batch(self.clock() + _IDLE)
        async with batch.lock:
            if self.open.get(key) is not batch:
                return False
            batch.expires = self.clock() + _IDLE
            if _CANCEL.fullmatch(text):
                del self.open[key]
                await event.send(event.plain_result("好，这段就放下"))
                return True
            end = _FINISH.fullmatch(text)
            if begin:
                text = text[begin.end():].lstrip(" ，,。！!：:；;")
            elif end:
                text = (end[1] or "").strip()
            quoted, current = image_refs(event)
            records = [(event, obj) for obj in getattr(event.message_obj, "message", ()) if type(obj).__name__ == "Record"]
            if text or current or quoted or records:
                if not end and (len(batch.pieces) >= 32 or sum(len(p.text) for p in batch.pieces) + len(text) > 24000):
                    await event.send(event.plain_result("这段已经很长了，这条还没收进去。先说「说完了」让我回前面的，再接着发吧"))
                    return True
                batch.pieces.append(Piece(text, list(dict.fromkeys(quoted + current)), records))
            if begin:
                if not re.search(r"不用回|别回|先别说话", text):
                    await event.send(event.plain_result("你发，我等你说完"))
            elif end:
                if not batch.pieces:
                    del self.open[key]
                    await event.send(event.plain_result("我这边还没收到前面的内容，你想让我看什么？"))
                    return True
                result = await self.answer(host, event, batch.pieces)
                await event.send(event.plain_result(result))
                del self.open[key]
            return True

    async def answer(self, host, event, pieces):
        evidence = []
        for piece in pieces:
            row = {"文字": piece.text} if piece.text else {}
            if piece.images:
                try:
                    row["图片"] = await observe_image(host, piece.images, "按顺序描述这些实际图片中可见的细节和文字。模糊或未读取的内容标明。")
                except Exception as exc:  # noqa: BLE001 -- preserve the other pieces when one attachment fails.
                    _LOG.info("等我说完图片未读到（%s）", type(exc).__name__)
                    row["图片"] = "本条图片未能读取"
            if piece.audio:
                row["语音"] = []
                for original, record in piece.audio:
                    try:
                        words, _ = await asyncio.wait_for(hear(host, original, record), 110)
                    except Exception as exc:  # noqa: BLE001 -- keep transcription failure distinct from quoted speech.
                        _LOG.info("等我说完语音未读到（%s）", type(exc).__name__)
                        words = ""
                    row["语音"].append(words or "本条语音未能辨认")
            evidence.append(row)
        provider = await host.context.get_using_provider_async(event.unified_msg_origin)
        async with asyncio.timeout(60):
            result = await provider.text_chat(
                system_prompt=QQ_PERSONA_PATH.read_text(encoding="utf-8-sig") +
                "\n对方刚分几条说完同一件事。结合按实际到达顺序给出的文字、转写和画面观察，自然接一次话。"
                "读不到的附件不要猜；不要宣称已经替对方执行了外部操作。",
                prompt=json.dumps(evidence, ensure_ascii=False), contexts=[])
        answer = natural_chat_text(str(result.completion_text or "").strip())
        if not answer:
            raise ValueError("empty reply for completed turn")
        return answer
