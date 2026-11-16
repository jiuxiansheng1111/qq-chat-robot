"""Public Yingdi APIs plus separately attributed ladder statistics.

Yingdi endpoints/parameters are used by its public hearthstone cards/userdecks JS.
The optional stats API is documented at https://api.kolodahearthstone.com/docs.
It publishes cached HSGuru datasets; community likes are never treated as wins.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.services.bounded_http import request_bytes, request_json

from .aliases import community_name, is_thief_priest, is_twenty_priest
from .cards import find_cards
from .decks import MODES, analyze, card_record, clean, replacement_candidates

YINGDI = "https://api2.iyingdi.com"
STATS = "https://api.kolodahearthstone.com"
_LOG = logging.getLogger(__name__)


def utc_now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def period_label(value):
    return {"past_day": "过去24小时", "black_empire": "黑暗帝国版本窗口",
            "black_empire_ban": "黑暗帝国禁用调整后窗口"}.get(value, str(value or "未说明"))


def age_seconds(value):
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            return math.inf
        age = time.time() - dt.timestamp()
        return age if age >= -300 else math.inf
    except (ValueError, TypeError):
        return math.inf


def source_meta(payload):
    meta = payload.get("meta") or {}
    if meta.get("stale") or age_seconds(meta.get("fetched_at")) > 48 * 3600:
        raise ValueError("统计快照已过期")
    return {"provider": "HSGuru（KolodaHS公开数据API缓存）",
            "fetched_at": meta.get("fetched_at"), "source_id": meta.get("source_id")}


def wilson(winrate, games):
    p, z = winrate / 100, 1.96
    return (p + z*z/(2*games) - z*math.sqrt(p*(1-p)/games + z*z/(4*games*games))) / (1+z*z/games)


def rank_rows(rows, sort, min_games):
    valid = []
    for row in rows:
        try:
            rate, pop, games = float(row["winrate"]), float(row["popularity"]), int(row["games"])
        except (TypeError, ValueError, KeyError):
            continue
        if not (math.isfinite(rate) and math.isfinite(pop) and 0 <= rate <= 100
                and 0 <= pop <= 100 and games >= min_games):
            continue
        valid.append({"archetype": clean(row.get("archetype"), 100), "winrate": rate,
                      "popularity": pop, "games": games, "winrate_lower_bound": wilson(rate, games)})
    pop_order = sorted(valid, key=lambda x: x["popularity"])
    for row in valid:
        percentile = sum(x["popularity"] <= row["popularity"] for x in pop_order) / max(1, len(valid))
        row["recommendation_score"] = round(.7 * row["winrate_lower_bound"] + .3 * percentile, 4)
    key = {"winrate": "winrate", "popularity": "popularity", "balanced": "recommendation_score"}[sort]
    return sorted(valid, key=lambda x: (x[key], x["games"]), reverse=True)


def class_matches(archetype, wanted):
    if not wanted:
        return True
    value = archetype.casefold()
    aliases = {
        "demonhunter": (" dh", "demon hunter"), "deathknight": (" dk", "death knight"),
        "warlock": ("warlock", "lock"), "rogue": ("rogue",), "paladin": ("paladin",),
        "priest": ("priest",), "mage": ("mage",), "shaman": ("shaman",),
        "warrior": ("warrior",), "hunter": ("hunter",), "druid": ("druid",),
    }
    return any(value.endswith(s) or s in value for s in aliases.get(wanted.casefold(), (wanted.casefold(),)))


_ARCHETYPE_WORDS = {
    "海盗": ("pirate",), "宇宙": ("highlander", " hl ", "reno"),
    "奇数": ("odd",), "偶数": ("even",), "任务": ("quest",),
    "弃牌": ("disco", "discard"), "机械": ("mech",), "元素": ("elemental",),
    "克苏恩": ("c'thun", "cthun"), "恩佐斯": ("n'zoth", "nzoth"),
    "艾雅": ("ayaya", "aya"), "拉法姆": ("rafaam",), "兆示": ("herald",),
    "圣契": ("libram",), "暗牧": ("shadow priest",), "野猪": ("boar",),
    "控制": ("control",), "快攻": ("aggro",), "奥秘": ("secret",),
    "青玉": ("jade",), "蛋": ("egg",), "龙": ("dragon",),
    "三血": ("blood",), "三冰": ("frost",), "三邪": ("unholy",), "彩虹": ("rainbow",),
}
_CLASS_SUFFIX = {
    "战": "Warrior", "法": "Mage", "贼": "Rogue", "骑": "Paladin", "德": "Druid",
    "牧": "Priest", "术": "Warlock", "萨": "Shaman", "猎": "Hunter", "瞎": "Demonhunter",
    "dk": "Deathknight",
}


def archetype_matches(name, query):
    """Aliases identify candidate families only; the returned stats retain their source name."""
    query = query.casefold().strip().replace("卡组", "")
    name = " " + name.casefold() + " "
    if query and query in name:
        return True
    suffix = next((value for key, value in _CLASS_SUFFIX.items() if query.endswith(key)), "")
    if suffix and not class_matches(name.strip(), suffix):
        return False
    descriptors = [choices for word, choices in _ARCHETYPE_WORDS.items() if word in query]
    if is_thief_priest(query):
        if not class_matches(name.strip(), "Priest"):
            return False
        descriptors.append(("thief", "azalina"))
    return bool(descriptors) and all(any(choice in name for choice in choices) for choices in descriptors)


class HearthstoneService:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.catalog = {}
        self.catalog_at = ""
        self._catalog_lock = asyncio.Lock()
        self._requests = asyncio.Semaphore(3)
        self._responses = {}
        self._retry_catalog_after = 0.0

    async def api(self, host, path, *, data=None, params=None, ttl=300):
        key = (host, path, json.dumps(data or params or {}, sort_keys=True, ensure_ascii=False))
        cached = self._responses.get(key)
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1]
        async with self._requests, httpx.AsyncClient(trust_env=False) as client:
            payload = await request_json(
                client, "POST" if data is not None else "GET", host + path,
                **({"data": data} if data is not None else {"params": params or {}}),
                headers={"User-Agent": "QQChat-Hearthstone/1.0", "Referer": "https://www.iyingdi.com/"}
                if host == YINGDI else {"User-Agent": "QQChat-Hearthstone/1.0"},
                timeout_seconds=12, max_bytes=2 * 1024 * 1024,
            )
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise ValueError("数据源未返回有效结果")
        if len(self._responses) >= 96:
            self._responses.pop(next(iter(self._responses)))
        self._responses[key] = time.monotonic(), payload
        return payload

    async def ensure_catalog(self):
        async with self._catalog_lock:
            path = self.cache_dir / "cards.json"
            if not self.catalog and path.exists() and path.stat().st_size < 16 * 1024 * 1024:
                try:
                    snapshot = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
                    self.catalog = {int(c["dbf_id"]): c for c in snapshot["cards"]}
                    self.catalog_at = snapshot["fetched_at"]
                except (ValueError, KeyError, TypeError, OSError):
                    self.catalog = {}
            if self.catalog and age_seconds(self.catalog_at) < 6 * 3600:
                return
            if time.monotonic() < self._retry_catalog_after:
                if self.catalog and age_seconds(self.catalog_at) < 48 * 3600:
                    return
                raise ValueError("卡牌库暂时无法更新")
            try:
                async with asyncio.timeout(35):
                    params = {"ignoreHero": 1, "statistic": "total", "order": "+id", "size": 500}
                    first = await self.api(YINGDI, "/hearthstone/card/search/vertical",
                                           data={**params, "page": 0}, ttl=0)
                    total = int(first["data"]["total"])
                    if not 1 <= total <= 20000:
                        raise ValueError("卡牌库数量异常")
                    records = list(first["data"]["cards"])
                    page_size = len(records)
                    if page_size != min(500, total):
                        raise ValueError("卡牌库分页长度变化")
                    pages = await asyncio.gather(*(self.api(
                        YINGDI, "/hearthstone/card/search/vertical",
                        data={**params, "page": page}, ttl=0,
                    ) for page in range(1, math.ceil(total / 500))))
                    for page in pages:
                        records.extend(page["data"]["cards"])
                    if len({int(c["id"]) for c in records}) != total:
                        raise ValueError("卡牌库分页不完整")
                    cards = [card_record(c) for c in records if int(c.get("gameid", 0)) > 0]
                    if not cards:
                        raise ValueError("卡牌库为空")
                    # On duplicate DBF ids, retain the newest public record.
                    cards.sort(key=lambda c: c.get("updated") or 0)
                    new = {c["dbf_id"]: c for c in cards}
                    snapshot = {"fetched_at": utc_now(), "source": YINGDI, "cards": list(new.values())}
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    temp = path.with_suffix(".tmp")
                    await asyncio.to_thread(temp.write_text, json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
                    temp.replace(path)
                    self.catalog, self.catalog_at = new, snapshot["fetched_at"]
            except Exception as exc:
                self._retry_catalog_after = time.monotonic() + 300
                _LOG.warning("炉石卡牌库刷新失败 error_type=%s", type(exc).__name__)
                if not self.catalog or age_seconds(self.catalog_at) > 48 * 3600:
                    raise ValueError("卡牌库暂时无法更新") from exc

    def catalog_source(self):
        return {"name": "旅法师营地", "url": "https://www.iyingdi.com/web/tools/hearthstone/cards",
                "fetched_at": self.catalog_at, "cached": True,
                "refresh_delayed": age_seconds(self.catalog_at) > 6 * 3600}

    async def lookup(self, query, mode=""):
        await self.ensure_catalog()
        matches, similar = await asyncio.to_thread(find_cards, self.catalog.values(), query, mode)
        return {"status": "ok" if matches else "not_found", "query": query, "mode": mode,
                "total": len(matches), "cards": matches[:8], "similar": similar, "source": self.catalog_source()}

    async def card_image(self, card):
        """Download the selected printing's full card face, including for older catalog snapshots."""
        record = self.catalog.get(card["dbf_id"], {})
        url = record.get("image_url")
        if not url:
            payload = await self.api(YINGDI, "/hearthstone/card/search/vertical",
                                     data={"name": card["name"], "ignoreHero": 1, "page": 0, "size": 50})
            url = next((c.get("img") for c in payload["data"]["cards"]
                        if str(c.get("gameid")) == str(card["dbf_id"])), None)
        if isinstance(url, str) and url.startswith("http://static.iyingdi.cn/"):
            url = "https://" + url.removeprefix("http://")
        if not isinstance(url, str) or not url.startswith(("https://pic.iyingdi.com/", "https://static.iyingdi.cn/")):
            raise ValueError("此版本没有可用的营地卡面")
        async with self._requests, httpx.AsyncClient(trust_env=False) as client:
            data = await request_bytes(client, "GET", url, max_bytes=4 * 1024 * 1024, timeout_seconds=8,
                                       headers={"Referer": "https://www.iyingdi.com/"})
        if not (data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))
                or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")):
            raise ValueError("营地卡面格式不正确")
        if record:
            record["image_url"] = url
        return data

    async def deck(self, code, mode=""):
        await self.ensure_catalog()
        return {"status": "ok", "deck": analyze(code, self.catalog, expected_mode=mode),
                "card_source": self.catalog_source()}

    async def yingdi_decks(self, mode, *, query="", player_class="", limit=3):
        params = {"format": MODES[mode], "order": "hot", "page": 0, "size": 30, "total": 1}
        if query:
            params["name"] = community_name(query)
        if player_class:
            params["faction"] = player_class
        response = await self.api(YINGDI, "/hearthstone/deck/search_open", params=params)
        await self.ensure_catalog()
        results = []
        for item in response.get("data", []):
            raw = item.get("deck", {})
            if raw.get("format") != MODES[mode]:
                continue
            updated = raw.get("updated")
            if not isinstance(updated, (int, float)) or not -300 <= time.time() - updated <= 14 * 86400:
                continue
            try:
                detail = analyze(str(raw.get("code", "")), self.catalog, expected_mode=mode)
            except ValueError:
                continue
            if not self.usable(detail):
                continue
            if is_twenty_priest(query) and not detail["deck_size"].get("opponent_copies"):
                continue
            if player_class and detail["class"].casefold() != player_class.casefold():
                continue
            results.append({"name": clean(raw.get("name"), 100), "deck": detail,
                            "source": "旅法师营地用户套牌（社区热榜，非天梯胜率榜）",
                            "url": f"https://www.iyingdi.com/web/tools/hearthstone/userdecks/deckdetail/{int(raw['id'])}",
                            "yingdi_id": int(raw["id"]), "updated_at": datetime.fromtimestamp(updated, UTC).isoformat(),
                            "community": {k: raw.get(k) for k in ("hotScore", "pageview", "likes", "reply")},
                            "author_notes": clean(raw.get("remark"), 1200), "winrate": None})
            if len(results) >= limit:
                break
        return {"status": "ok" if results else "not_found", "mode": mode, "sort": "community",
                "decks": results, "card_source": self.catalog_source(), "checked_at": utc_now()}

    async def yingdi_detail(self, deck_id, mode=""):
        payload = await self.api(YINGDI, f"/hearthstone/deck/{int(deck_id)}", params={"format": "json"})
        raw = payload.get("deck") or {}
        result = await self.deck(str(raw.get("code", "")), mode)
        result.update(name=clean(raw.get("name"), 100), author_notes=clean(raw.get("remark"), 1800),
                      url=f"https://www.iyingdi.com/web/tools/hearthstone/userdecks/deckdetail/{int(deck_id)}")
        return result

    @staticmethod
    def usable(deck):
        check = deck["validation"]
        return (check["complete"] and not check["outside_current_card_pool"] and
                not check["over_copy_limit"] and check["runes_valid"] and
                check.get("deck_size_valid", deck["total_cards"] in (30, 40)))

    async def recommend(self, mode, *, sort="balanced", player_class="", limit=3, min_games=500, archetype_query=""):
        if not player_class and is_thief_priest(archetype_query):
            player_class = "Priest"
        if sort == "community":
            return await self.yingdi_decks(mode, query=archetype_query, player_class=player_class, limit=limit)
        try:
            payload = await self.api(STATS, "/v1/hsguru/meta",
                                     params={"format": mode, "rank": "all", "period": "past_day",
                                             "min_games": min_games}, ttl=900)
            source = source_meta(payload)
            data = payload["data"]
            if data.get("format") != mode or data.get("rank") != "all" or data.get("period") != "past_day":
                raise ValueError("统计口径不匹配")
            rows = [row for row in rank_rows(data.get("items", []), sort, min_games)
                    if class_matches(row["archetype"], player_class)
                    and (not archetype_query or archetype_matches(row["archetype"], archetype_query))]
        except (ValueError, KeyError, TypeError, httpx.HTTPError, TimeoutError) as exc:
            _LOG.warning("炉石对局统计不可用 error_type=%s", type(exc).__name__)
            fallback = await self.yingdi_decks(mode, query=archetype_query, player_class=player_class, limit=limit)
            fallback.update(status="partial", requested_sort=sort,
                            limitation="本轮无法取得新鲜且同口径的胜率数据；以下仅为营地社区热榜，不宣称按胜率推荐")
            return fallback
        if archetype_query and not rows:
            named = await self.yingdi_decks(mode, query=archetype_query, player_class=player_class, limit=2)
            return {"status": "no_matching_statistics", "mode": mode, "query": archetype_query,
                    "decks": named.get("decks", []), "statistics": source,
                    "available_archetypes": [r["archetype"] for r in data.get("items", [])],
                    "limitation": "该名称未匹配到足够样本的同类统计。可根据真实构筑分析强弱；不能借用另一类卡组的胜率"}
        await self.ensure_catalog()

        async def build(row):
            try:
                builds = await self.api(STATS, "/v1/constructed/hsguru-deck",
                                        params={"archetype": row["archetype"], "format_name": mode, "rank": "all"}, ttl=900)
                build_source = source_meta(builds)
                candidates = sorted(builds.get("data", []), key=lambda b: int(b.get("games") or 0), reverse=True)
                for b in candidates:
                    if (str(b.get("format", "")).casefold() != mode or
                            b.get("archetype") != row["archetype"] or
                            age_seconds(b.get("updated_at")) > 48 * 3600):
                        continue
                    if player_class and str(b.get("class", "")).casefold() != player_class.casefold():
                        continue
                    try:
                        detail = analyze(str(b.get("deck_code", "")), self.catalog, expected_mode=mode)
                    except ValueError:
                        continue
                    if not self.usable(detail):
                        continue
                    if is_twenty_priest(archetype_query) and not detail["deck_size"].get("opponent_copies"):
                        continue
                    return {"name": row["archetype"], "archetype_stats": row,
                            "display_name": (("宇宙" if "hl " in row["archetype"].casefold() else "") + "20脏牧")
                            if detail["deck_size"].get("opponent_copies") else row["archetype"],
                            "deck": detail, "url": clean(b.get("url"), 300),
                            "deck_source": "HSGuru具体构筑，经KolodaHS公开数据API读取；卡牌属性另由营地核对",
                            "build_stats": {"winrate": b.get("win_rate"), "games": b.get("games"),
                                            "period": b.get("sample_period"), "rank": b.get("sample_rank"),
                                            "period_label": period_label(b.get("sample_period")),
                                            "updated_at": b.get("updated_at"), "source": build_source},
                            "stats_note": "排序依据是同类卡组过去一天的表现；此代码的构筑统计窗口单列，不混用"}
            except (ValueError, KeyError, TypeError, httpx.HTTPError, TimeoutError) as exc:
                _LOG.info("炉石构筑未获取 archetype=%s error_type=%s", row["archetype"], type(exc).__name__)
            return None

        chosen = []
        # Bounded lookups, keeping the sorted order even if requests finish out of order.
        for start in range(0, min(len(rows), 9), 3):
            found = await asyncio.gather(*(build(row) for row in rows[start:start + 3]))
            chosen.extend(x for x in found if x)
            if len(chosen) >= limit:
                break
        return {
            "status": "ok" if chosen else "no_verified_builds", "mode": mode, "mode_name": MODES[mode],
            "sort": sort, "min_games": min_games, "decks": chosen[:limit],
            "ranking_basis": {"winrate": "同类卡组胜率降序；同胜率按样本量",
                              "popularity": "同类卡组对局占比降序；同占比按样本量",
                              "balanced": "70%胜率的95% Wilson置信下界 + 30%热度分位；最低500场"}[sort],
            "statistics": {**source, "url": data.get("source_url"), "period": "past_day",
                           "period_label": "过去24小时", "rank": "all", "rank_label": "全分段",
                           "region": "来源未单列地区，不等同国服统计",
                           "patch_window": payload.get("meta", {}).get("current_patch_period")},
            "card_source": self.catalog_source(), "checked_at": utc_now(),
        }

    async def evaluate_code(self, code, mode=""):
        result = await self.deck(code, mode)
        detail = result["deck"]
        if not self.usable(detail):
            result["status"] = "invalid_deck"
            return result
        try:
            payload = await self.api(STATS, "/v1/hsguru/meta", params={
                "format": detail["mode"], "rank": "all", "period": "past_day", "min_games": 500}, ttl=900)
            source = source_meta(payload)
            data = payload["data"]
            if data.get("format") != detail["mode"] or data.get("rank") != "all" or data.get("period") != "past_day":
                raise ValueError("统计口径不匹配")
            rows = [r for r in rank_rows(data.get("items", []), "popularity", 500)
                    if class_matches(r["archetype"], detail["class"])]

            def fingerprint(d):
                return (sorted((c["name"], c["count"]) for c in d["cards"]),
                        sorted((c["name"], c["count"], c["owner_dbf_id"]) for c in d["sideboards"]))

            async def check(row):
                try:
                    builds = await self.api(STATS, "/v1/constructed/hsguru-deck", params={
                        "archetype": row["archetype"], "format_name": detail["mode"], "rank": "all"}, ttl=900)
                    build_source = source_meta(builds)
                    for b in builds.get("data", []):
                        if b.get("archetype") != row["archetype"] or str(b.get("format", "")).casefold() != detail["mode"]:
                            continue
                        try:
                            candidate = analyze(str(b.get("deck_code", "")), self.catalog, expected_mode=detail["mode"])
                        except ValueError:
                            continue
                        if self.usable(candidate) and fingerprint(candidate) == fingerprint(detail):
                            return {"archetype_stats": row, "build_stats": {
                                "winrate": b.get("win_rate"), "games": b.get("games"),
                                "rank": b.get("sample_rank"), "period": b.get("sample_period"),
                                "period_label": period_label(b.get("sample_period")),
                                "source": build_source, "url": b.get("url")}}
                except (ValueError, KeyError, TypeError, httpx.HTTPError, TimeoutError):
                    pass
                return None

            matches = await asyncio.gather(*(check(r) for r in rows[:9]))
            result["exact_build_statistics"] = next((m for m in matches if m), None)
            result["statistics_source"] = {**source, "url": data.get("source_url"),
                                           "period": "past_day", "rank": "all"}
            if not result["exact_build_statistics"]:
                result["limitation"] = "已检索的当前统计中没有完全相同构筑；基于真实卡牌评估打法与优劣，不编造该代码胜率"
        except (ValueError, KeyError, TypeError, httpx.HTTPError, TimeoutError):
            result["limitation"] = "本轮未取得可用的对局统计；仍可依据真实构筑说明强弱、费用和合成建议"
        return result

    async def replace(self, code, target, mode="", budget=None):
        result = await self.deck(code, mode)
        if not self.usable(result["deck"]):
            result["status"] = "invalid_deck"
            return result
        result["replacement"] = replacement_candidates(result["deck"], target, self.catalog, budget=budget)
        return result
