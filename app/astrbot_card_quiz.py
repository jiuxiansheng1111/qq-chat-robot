"""A QQ room plays against one real collectible card illustration at a time."""
from __future__ import annotations

import asyncio
import io
import logging
import random
import re
from dataclasses import dataclass, field

import httpx
from PIL import Image, ImageOps

from app.astrbot_hearthstone import service
from app.astrbot_turn_features import context_for, enabled_parts
from app.services.hearthstone.cards import matches_card_answer
from app.services.hearthstone.decks import CLASSES

_LOG = logging.getLogger(__name__)
_START = re.compile(r"(?:肥鱼[，, ]*)?(?:来(?:一)?[局轮把场]|开始(?:一[局轮])?|玩(?:一[局轮把])?|一起玩)?"
                    r"(?:猜(?:一)?(?:张)?(?:炉石(?:传说)?)?卡(?:牌)?|炉石猜卡)(?:游戏)?(?:[，, ]*(标准|狂野)(?:模式)?)?[吧吗呀]?$")
_HINT = re.compile(r"(?:给(?:我|个|点|一个)?|再给(?:个|点|一个)?|来(?:个|点))?(?:猜卡)?提示(?:一下)?[吧呀]?$")
_END = re.compile(r"(?:揭晓(?:答案)?|公布答案|结束猜卡|不猜了|猜不出(?:来)?了|这张是什么卡)$")
_GUESS = re.compile(r"(?:我猜(?:是)?|我觉得是|答案是|是不是|是)(.{1,65}?)(?:这张|吗|吧)?$")
_FETCH_ERRORS = (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, TimeoutError, Image.DecompressionBombError)


def art_crop(data):
    # The center of the top illustration excludes the mana gem, nameplate, rules and stats.
    with Image.open(io.BytesIO(data)) as raw:
        if raw.width * raw.height > 10_000_000 or not 0.55 < raw.width / raw.height < 0.9 or raw.width < 140:
            raise ValueError("unrecognized card frame")
        im = ImageOps.exif_transpose(raw).convert("RGBA").convert("RGB")
        w, h = im.size
        art = im.crop((round(w * .32), round(h * .19), round(w * .69), round(h * .39)))
        art.thumbnail((620, 440))
        if max(high - low for low, high in art.getextrema()) < 12:
            raise ValueError("empty illustration")
        output = io.BytesIO()
        art.save(output, "PNG")
        return output.getvalue()


@dataclass
class Round:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    card: dict | None = None
    face: bytes = b""
    hint: int = 0
    timer: asyncio.Task | None = None


class CardQuiz:
    title = "炉石猜卡"

    def __init__(self, *, interval=30):
        self.rooms = {}
        self.interval = interval

    def interested(self, key, text, addressed):
        if not key[2]:
            return False
        text = text.strip(" 。！？!?～")
        if _START.fullmatch(text):
            return True
        game = self.rooms.get(key[:3])
        if not game:
            return False
        guess = _GUESS.fullmatch(text)
        return bool(_HINT.fullmatch(text) or _END.fullmatch(text)
                    or guess and text.startswith(("我猜", "答案是", "我觉得是"))
                    or game.card and matches_card_answer(game.card, guess[1] if guess else text))

    async def close(self):
        tasks = [g.timer for g in self.rooms.values() if g.timer]
        self.rooms.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def draw(self, host, mode):
        source = service(host)
        try:
            await source.ensure_catalog()
        except _FETCH_ERRORS:
            if not source.catalog:
                raise
        pool = [c for c in source.catalog.values() if c.get("collectible") and c.get("name")
                and c.get("type") in {"法术", "随从", "武器"} and (not mode or c.get(mode))]
        if not pool:
            raise ValueError("no eligible cards")
        for card in random.sample(pool, min(len(pool), 4)):
            try:
                face = await source.card_image(card)
                art = await asyncio.to_thread(art_crop, face)
            except _FETCH_ERRORS:
                continue
            return card, face, art
        raise ValueError("no readable illustration")

    async def handle(self, host, event, key, text):
        from astrbot.api.message_components import Image as QQImage
        from astrbot.api.message_components import Plain

        room = key[:3]
        text = text.strip(" 。！？!?～")
        start = _START.fullmatch(text)
        game = self.rooms.get(room)
        if game is None:
            if not start:
                return False
            if len(self.rooms) >= 128:
                await event.send(event.plain_result("正在玩的群有点多，过会儿再开一局吧"))
                return True
            game = self.rooms[room] = Round()
        async with game.lock:
            if self.rooms.get(room) is not game:
                return True
            if start and not game.card:
                try:
                    async with asyncio.timeout(45):
                        card, face, art = await self.draw(host, {"标准": "standard", "狂野": "wild"}.get(start[1], ""))
                    await event.send(event.chain_result([Plain("猜猜这是哪张炉石卡？直接报卡名或简称就行"), QQImage.fromBytes(art)]))
                except BaseException:
                    self.rooms.pop(room, None)
                    raise
                game.card, game.face = card, face
                game.timer = asyncio.create_task(self._clock(host, event, room, game))
            elif start:
                await event.send(event.plain_result("这张还没人猜中，想要线索就说「给个提示」"))
            elif _END.fullmatch(text):
                await self.reveal(event, room, game, winner="")
            elif _HINT.fullmatch(text):
                await self.give_hint(event, room, game)
            else:
                guess = _GUESS.fullmatch(text)
                answer = (guess[1] if guess else text).strip(" 《》「」")
                if matches_card_answer(game.card, answer):
                    who = event.message_obj.raw_message.get("sender") or {}
                    await self.reveal(event, room, game, winner=who.get("card") or who.get("nickname") or "你")
                elif guess:
                    await event.send(event.plain_result("这张没猜中，再看看卡画？"))
                else:
                    return False
            return True

    async def give_hint(self, event, room, game):
        card = game.card
        if game.hint == 3:
            await self.reveal(event, room, game, winner="")
            return
        effect = card.get("effect") or "卡上没有效果文字"
        for name in (card["name"], card.get("english_name")):
            if name:
                effect = re.sub(re.escape(name), "这张牌", effect, flags=re.IGNORECASE)
        classes = "／".join(CLASSES.get(c.strip().capitalize(), c.strip()) for c in re.split(r"[,;/|]", card["class"]) if c.strip())
        hints = (f"先看费用，{card['mana']} 费", f"职业是{classes or '中立'}", "效果线索：" + effect)
        await event.send(event.plain_result(hints[game.hint]))
        game.hint += 1

    async def reveal(self, event, room, game, winner):
        from astrbot.api.message_components import Image as QQImage
        from astrbot.api.message_components import Plain

        card = game.card
        lead = f"{winner}猜中了！" if winner else "揭晓啦，"
        text = f"{lead}这张是《{card['name']}》，{card['mana']} 费，出自{card.get('set') or '未收录系列'}"
        await event.send(event.chain_result([Plain(text), QQImage.fromBytes(game.face)]))
        self.rooms.pop(room, None)
        if game.timer and game.timer is not asyncio.current_task():
            game.timer.cancel()

    async def _clock(self, host, event, room, game):
        try:
            while self.rooms.get(room) is game:
                await asyncio.sleep(self.interval)
                async with game.lock:
                    if self.rooms.get(room) is not game:
                        return
                    parts = enabled_parts(host, event)
                    config = getattr(host.context, "get_config", lambda _: {})(event.unified_msg_origin)
                    permitted = config.get("plugin_set", ["*"])
                    required = {"astrbot_plugin_qqchat"} if parts is None else {"astrbot_plugin_qqchat_core", "astrbot_plugin_qqchat_card_quiz"}
                    if (not host.runtime or parts is not None and "card_quiz" not in parts or context_for(host, event) is None
                            or not host._get_group_router().allows_companion_turn(event, False)
                            or "*" not in permitted and not required.issubset(permitted)
                            or not await host._legacy_event_allowed(event.message_obj.raw_message)):
                        return
                    await self.give_hint(event, room, game)
        except Exception as exc:  # noqa: BLE001 -- a paused or disconnected room must not produce repeated sends.
            _LOG.info("猜卡回合已收起（%s）", type(exc).__name__)
        finally:
            if self.rooms.get(room) is game:
                self.rooms.pop(room, None)
