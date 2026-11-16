"""Arena offered-card comparisons, kept separate from constructed legality.

Primary references checked 2026-11-05:
https://www.heartharena.com/blog/the-big-heartharena-tierlist-overhaul
https://hearthstone.blizzard.com/en-us/news/23882860/25-0-patch-notes
https://news.blizzard.com/en-us/article/24266874/35-4-patch-notes
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import Counter
from html.parser import HTMLParser

import httpx

from app.services.bounded_http import request_bytes

from .cards import normalized_name
from .decks import CLASSES, clean, roles, rune_info
from .service import STATS, age_seconds, source_meta, utc_now

RATINGS_URL = "https://www.heartharena.com/zh-cn/tierlist"
_SLUGS = {**{k.lower(): k for k in CLASSES}, "death-knight": "Deathknight",
          "demon-hunter": "Demonhunter", "neutral": "Neutral"}


class RatingsParser(HTMLParser):
    """Read actual class tables; ignore navigation, scripts and changelog tables."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current_class = None
        self.sections = []
        self.reading = None
        self.name = ""
        self.score = ""
        self.ratings = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section":
            self.sections.append(self.current_class)
            if "tierlist" in attrs.get("class", "").split():
                self.current_class = _SLUGS.get(attrs.get("id"))
            elif attrs.get("id") or "changelog" in attrs.get("class", ""):
                self.current_class = None
        if self.current_class and tag == "dt":
            self.name, self.score, self.reading = "", "", "name"
        elif self.current_class and tag == "dd" and "score" in attrs.get("class", "").split():
            self.reading = "score"

    def handle_data(self, data):
        if self.reading == "name" and not self.name and data.strip():
            self.name = clean(data, 100)
        elif self.reading == "score":
            self.score += data

    def handle_endtag(self, tag):
        if tag == "dt":
            self.reading = None
        if tag == "dd":
            if self.current_class and self.name and re.fullmatch(r"\s*\d{1,3}\s*", self.score):
                self.ratings.setdefault(self.current_class, {})[normalized_name(self.name)] = int(self.score)
            self.reading = None
        if tag == "section" and self.sections:
            self.current_class = self.sections.pop()


class ArenaService:
    def __init__(self, hearthstone):
        self.hs = hearthstone
        self._ratings = None
        self._lock = asyncio.Lock()
        self._retry_after = 0.0

    async def ratings(self):
        path = self.hs.cache_dir / "arena-ratings.json"
        async with self._lock:
            if self._ratings is None and path.exists() and path.stat().st_size < 3 * 1024 * 1024:
                try:
                    data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
                    if isinstance(data.get("ratings"), dict):
                        self._ratings = data
                except (ValueError, OSError):
                    pass
            if self._ratings and age_seconds(self._ratings.get("fetched_at")) < 86400:
                return self._ratings
            try:
                if time.monotonic() < self._retry_after:
                    raise ValueError("评分源冷却")
                async with httpx.AsyncClient(trust_env=False) as client:
                    raw = await request_bytes(client, "GET", RATINGS_URL, max_bytes=3 * 1024 * 1024,
                                              timeout_seconds=12, headers={"User-Agent": "QQChat-Arena/1.0"})
                text = raw.decode("utf-8")
                parser = RatingsParser()
                parser.feed(text)
                if len(parser.ratings) < 11 or sum(map(len, parser.ratings.values())) < 500:
                    raise ValueError("评分表结构变化")
                date = re.search(r"(20\d{2})/(\d{1,2})/(\d{1,2}):", text)
                self._ratings = {"ratings": parser.ratings, "fetched_at": utc_now(),
                                 "last_changelog": f"{date[1]}-{int(date[2]):02}-{int(date[3]):02}" if date else None}
                self.hs.cache_dir.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix(".tmp")
                await asyncio.to_thread(temp.write_text, json.dumps(self._ratings, ensure_ascii=False), encoding="utf-8")
                temp.replace(path)
                return self._ratings
            except (httpx.HTTPError, TimeoutError, ValueError, OSError):
                self._retry_after = time.monotonic() + 300
                if self._ratings and age_seconds(self._ratings.get("fetched_at")) < 3 * 86400:
                    return {**self._ratings, "refresh_delayed": True}
                return {"ratings": {}, "unavailable": True, "fetched_at": None}

    async def resolve(self, names):
        await self.hs.ensure_catalog()
        cards, unresolved = [], []
        for name in names:
            key = normalized_name(name)
            matches = [c for c in self.hs.catalog.values() if key in {
                normalized_name(c["name"]), normalized_name(c["english_name"]), str(c["dbf_id"])}]
            if not matches:
                unresolved.append(name)
                continue
            # Same-name original/Core print: retain the newest known rules text.
            matches.sort(key=lambda c: (int(c.get("updated") or 0), c["standard"]), reverse=True)
            cards.append(matches[0])
        return cards, unresolved

    async def class_statistics(self, classes):
        try:
            payload = await self.hs.api(STATS, "/v1/arena/classes", ttl=1800)
            source = source_meta(payload)
            source["provider"] = "HSReplay竞技场职业统计（KolodaHS公开缓存）"
            entries = []
            for c in payload.get("data", []):
                if str(c.get("class", "")).casefold() not in {v.casefold() for v in classes}:
                    continue
                rate, drafts = float(c["win_rate"]), int(c["num_drafts"])
                if math.isfinite(rate) and 0 <= rate <= 100 and drafts > 0:
                    entries.append({"class": c["class"], "winrate": rate, "draft_samples": drafts,
                                    "url": "https://hsreplay.net/arena/" + c["class"].lower() + "/"})
            return {"source": source, "classes": entries,
                    "scope": "职业汇总，不是单卡胜率；接口未给出分段/地区/统计窗口，不用它给单卡排高低"}
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, OverflowError):
            return {"status": "unavailable"}

    async def compare(self, options, *, classes=(), drafted=()):
        flat = [name for group in options for name in group]
        resolved, unresolved = await self.resolve(flat)
        if unresolved:
            return {"status": "unrecognized_cards", "names": unresolved,
                    "message": "这些卡名还未核实；根据选牌截图核对文字或补准确卡名，不能猜成另一张牌"}
        existing, unknown_existing = await self.resolve(drafted)
        if unknown_existing:
            return {"status": "unrecognized_drafted_cards", "names": unknown_existing}
        candidates, offset = [], 0
        for group in options:
            candidates.append(resolved[offset:offset + len(group)])
            offset += len(group)
        inferred = set()
        for c in resolved + existing:
            inferred.update(f for f in re.split(r"[,;/|]", c["class"]) if f and f.lower() != "neutral")
        # Multiple offered classes can be a dual-class season: never silently select one.
        effective = list(classes) or (sorted(inferred) if len(inferred) == 1 else [])
        ratings, stats = await asyncio.gather(self.ratings(), self.class_statistics(effective))
        choices = []
        for index, cards in enumerate(candidates, 1):
            entries = []
            for c in cards:
                scores = {cl: ratings["ratings"].get(cl, {}).get(normalized_name(c["name"]))
                          for cl in effective or ("Neutral",)}
                entries.append({**c, "roles": roles(c), "heartharena_scores": scores,
                                "already_picked": sum(x["name"] == c["name"] for x in existing)})
            incompatible = [c["name"] for c in cards if effective and not
                            ({v.casefold() for v in re.split(r"[,;/|]", c["class"])} &
                             {"neutral", *(v.casefold() for v in effective)})]
            choices.append({"option": index, "cards": entries,
                            "runes_after_pick": rune_info(existing + cards),
                            "class_mismatch": incompatible})
        return {"status": "ok", "mode": "arena", "classes": effective,
                "class_confirmation_needed": not effective, "options": choices,
                "draft": summarize(existing), "card_source": self.hs.catalog_source(),
                "ratings_source": {"name": "HearthArena职业单卡基础评分", "url": RATINGS_URL,
                                   **{k: v for k, v in ratings.items() if k != "ratings"},
                                   "kind": "评分，不是胜率或百分数；基础评分不含本套配合，更新日志日期与采集日期分开"},
                "class_statistics": stats,
                "selection_guidance": {
                    "source": "https://www.heartharena.com/blog/the-big-heartharena-tierlist-overhaul",
                    "points": ["先比较独立强度，再看费用利用、灵活性与即时影响",
                               "考虑低费站场、解场、补牌、回复及终结手段；越接近选完越重视已选牌的缺口",
                               "只有实际已选的卡能支撑配合；未来可能抽到或选到的不当作已有资源",
                               "传说卡包比较整个组合；双职业评分分别列示，不把两份评分相加",
                               "竞技场允许多于两张同名牌，不套用标准/狂野系列轮换或构筑复制上限",
                               "死亡骑士仍按全套各色最大符文需求合计不超过三枚；以当前可选画面核对冲突"]}}


def parse_options(value):
    value = str(value or "").strip()
    if not value:
        return []
    groups = re.split(r"\s*(?:\||｜|;|；|、|\s/\s)\s*", value)
    options = [[v.strip() for v in re.split(r"\s*\+\s*", group) if v.strip()] for group in groups]
    if not 1 <= len(options) <= 3 or any(not 1 <= len(group) <= 6 for group in options):
        raise ValueError("用 | 分开三组选项，组合内卡牌用 + 分开")
    if any(len(name) > 100 for group in options for name in group):
        raise ValueError("请只提供卡名")
    return options


def summarize(cards):
    curve, functions, counts = Counter(), Counter(), Counter()
    for card in cards:
        curve[str(card["mana"]) if card["mana"] < 7 else "7+"] += 1
        counts[card["name"]] += 1
        functions.update(roles(card))
    return {"picked": len(cards), "remaining_to_30": max(0, 30-len(cards)),
            "cards": [{**c, "count": counts[c["name"]]} for i, c in enumerate(cards)
                      if c["name"] not in {x["name"] for x in cards[:i]}],
            "mana_curve": dict(curve), "effect_roles": dict(functions), "runes": rune_info(cards),
            "early_minions": sum(c["type"] == "随从" and c["mana"] <= 2 for c in cards)}
