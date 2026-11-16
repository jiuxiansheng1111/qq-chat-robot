"""Resolve concrete follow-up edits against the last actual lookup for this speaker."""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass

from app.astrbot_group_chat import _COMMAND, _human_message_text, _strip_name
from app.astrbot_listening import ALIASES
from app.astrbot_turn_features import enabled_parts
from app.services.social_plans import scope

_PREFIX = r"(?:(?:不对|不是这个|不是这[张首个]|不是天气|弄错了)[，, ]*)?(?:(?:我(?:说|指|想查|要)的(?:是)?|我要的是|换成|是)[，, ]*)?"
_FORMAT = re.compile(_PREFIX + r"(标准|狂野)(?:模式|版本|的)?(?:的)?(?:那张|那版|版本|卡牌)?[呀啊吧]?$")
_CARD = re.compile(r"(?:我说的(?:卡|卡牌)是|卡名是|我想查的卡是|改查)(.{1,50})$")
_ARTIST = re.compile(_PREFIX + r"([\w·• -]{1,32}?)(?:唱的(?:那首)?|的那首(?:歌)?|那首(?:歌)?)$")
_TITLE = re.compile(r"(?:不是天气[，, ]*)?(?:我说的是|我想听(?:的是)?|歌名是|我要听)?\s*([^《》]{0,24}?)"
                    r"(?:唱的|的)?《([^《》]{1,80})》(?:那首(?:歌)?)?$")
_CITY = re.compile(r"(?:我说的是|我问的是|我想查的是)(.{2,24}?)(?:的天气|天气)$")
_WRONG = re.compile(r"(?:不对|不是这个|不是这[张首个]|弄错了|你搞错了|我说的不是这个)[啊呀吧]?$")
_LOOSE = re.compile(r"(?:给我|帮我)?放个[ \t]*[《「]?([^《》「」\n，。！？?]{1,40}?)[》」]?$")


@dataclass
class Lookup:
    domain: str
    subject: str
    expires: float
    mode: str = ""
    retry: object = None


class Corrections:
    title = "改正刚才的查询"

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.items = OrderedDict()

    def remember(self, key, domain, subject, *, mode="", retry=None):
        self.items[key] = Lookup(domain, subject, self.clock() + 180, mode, retry)
        self.items.move_to_end(key)
        if len(self.items) > 256:
            self.items.popitem(last=False)

    def record(self, event, domain, name, **options):
        loose = _LOOSE.fullmatch(_strip_name(_human_message_text(event), ALIASES)[0].strip(" 。！？!?"))
        if domain == "weather" and loose:
            self.remember(scope(event), "unspecified", loose[1])
        else:
            self.remember(scope(event), domain, name, **options)

    def interested(self, key, text, addressed):
        item = self.items.get(key)
        if item and item.expires < self.clock():
            del self.items[key]
            item = None
        return bool(item or _LOOSE.fullmatch(text.strip(" 。！？!?～")))

    async def handle(self, host, event, key, text):
        text = text.strip(" 。！？!?～")
        loose = _LOOSE.fullmatch(text)
        if loose:
            # This is only the literal object of “放个…”. Its domain remains unresolved.
            self.remember(key, "unspecified", loose[1])
            return False
        item = self.items.get(key)
        if not item:
            return False
        enabled = enabled_parts(host, event)
        def allowed(slug):
            return enabled is None or slug in enabled
        if _WRONG.fullmatch(text):
            questions = {"cards": "是卡名不对，还是你想看标准或狂野的那一版？",
                         "music": "是歌名不对，还是想听另一位歌手唱的版本？",
                         "weather": "你想改哪个城市，还是刚才说的是别的东西？"}
            await event.send(event.plain_result(questions.get(item.domain, "你刚才指的是什么？")))
            return True
        if item.domain == "cards" and allowed("cards") and item.retry:
            mode, card = _FORMAT.fullmatch(text), _CARD.fullmatch(text)
            if mode or card:
                target = ("wild" if mode[1] == "狂野" else "standard") if mode else item.mode
                return await item.retry(host, event, target, card[1].strip(" 《》") if card else item.subject)
        if item.domain == "weather" and allowed("weather") and item.retry:
            city = _CITY.fullmatch(text)
            contrast = re.fullmatch(r"不是(.{1,24}?)[，, ]+是(.{1,24})", text)
            if city or contrast and contrast[1] == item.subject:
                return await item.retry(host, event, city[1] if city else contrast[2])
        artist, title = _ARTIST.fullmatch(text), _TITLE.fullmatch(text)
        if item.domain in {"music", "weather", "unspecified"} and (artist or title):
            if not allowed("music"):
                await event.send(event.plain_result("这间会话还没启用点歌"))
                return True
            if artist and item.domain == "weather":
                await event.send(event.plain_result(f"你说的是{artist[1]}的哪首歌？可以回我《歌名》"))
                self.remember(key, "artist", artist[1])
                return True
            if title:
                return await self.music(host, event, title[2], title[1].strip().removesuffix("的"))
            return await self.music(host, event, item.subject, artist[1].strip())
        if item.domain == "artist" and allowed("music"):
            named = re.fullmatch(r"歌名(?:叫|是)[：: ]?(.{1,50})", text)
            if title or named:
                return await self.music(host, event, title[2] if title else named[1], item.subject)
        # Let ordinary questions and the original tool's candidate selection proceed.
        if not re.fullmatch(r"(?:确认|选歌\s*\d+|选?\d+|第[一二三四五六七八九十\d]+张)", text):
            self.items.pop(key, None)
        return False

    async def music(self, host, event, song, artist):
        query = " ".join(part for part in (song, artist) if part)
        previous = event.get_extra(_COMMAND, None)
        event.set_extra(_COMMAND, "点歌 " + query)
        try:
            await host.on_onebot_message(event)
        finally:
            event.set_extra(_COMMAND, previous)
        actual = self.items.get(scope(event))
        if actual and actual.domain == "music" and actual.subject == query:
            actual.subject = song
        return True
