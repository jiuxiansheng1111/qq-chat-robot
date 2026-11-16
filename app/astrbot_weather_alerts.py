"""City preferences and next-day weather notices requested by individual QQ users."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from enum import IntFlag
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.social_plans import (
    CHINA,
    PlanStore,
    original_qq_client,
    scheduled_allowed,
    scope,
    send_personal_notice,
)
from app.services.weather import WeatherServiceError, fetch_weather

_LOG = logging.getLogger(__name__)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    owner TEXT PRIMARY KEY, origin TEXT NOT NULL, city TEXT NOT NULL, kinds INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cities (owner TEXT PRIMARY KEY, city TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notices (
    owner TEXT NOT NULL, city TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY(owner,city,day));
"""
_CITY = r"[\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z .'-]{0,39}"
_CONDITION = r"(?:下雨|有雨|降雨|大雨|降温|变冷|升温|变热|变天|温差(?:比较大|很大|大)?|气温变化)"
_CONDITIONS = rf"{_CONDITION}(?:(?:或者|或是|或|和|、){_CONDITION})*"
_CANCEL = re.compile(r"(?:取消|关闭|停掉|停止|关掉|不用|不要|别)(?:再)?(?:给我发|给我|发|我的)?天气提醒(?:了)?")
_STATUS = re.compile(r"(?:查看|看看|查询)?(?:我的)?天气提醒(?:状态|设置|开了吗|开了没)?")
_SET = re.compile(rf"(?:记住|设置|设定)?(?:我的)?天气城市(?:是|用|改为|设为|设成|改成|为|[ ：:])+({_CITY})")
_FORGET = re.compile(r"(?:清除|忘记|删除)(?:我的)?天气城市")
_ENABLE = re.compile(rf"(?:帮我|给我)?(?:开(?:启|通)?|订阅|设置)(?:一下)?(?:(?P<city>{_CITY}?)的?)?天气提醒")
_EXPLICIT = re.compile(rf"天气提醒[ ：:]+(?P<city>{_CITY})")
_WHEN = re.compile(rf"(?:以后|如果|要是)?\s*(?P<city>{_CITY}?)?(?:如果|要是)?(?P<conditions>{_CONDITIONS})(?:的时候|的话|时|了)?[，, ]*(?:就|记得|请|要)?(?:提前)?提醒(?:一下)?我(?:一下|一声|带伞)?")
_REVERSE = re.compile(rf"(?:以后|记得|请)?提醒我[ ，,]*(?P<city>{_CITY}?)(?:如果|要是)?(?P<conditions>{_CONDITIONS})(?:的时候|时)?")
_RAIN = re.compile(r"雨|雷暴|rain|drizzle|thunder", re.IGNORECASE)
_SNOW = re.compile(r"雪|snow|blizzard|ice pellets", re.IGNORECASE)


class Change(IntFlag):
    RAIN = 1
    COLDER = 2
    WARMER = 4
    ALL = RAIN | COLDER | WARMER


def parse_request(text):
    text = text.strip(" \t？?！!。吧呀哦").removeprefix("/").strip()
    if _CANCEL.fullmatch(text):
        return "cancel", "", Change.ALL
    if _STATUS.fullmatch(text):
        return "status", "", Change.ALL
    if _FORGET.fullmatch(text):
        return "forget", "", Change.ALL
    if match := _SET.fullmatch(text):
        return "city", match[1].strip(), Change.ALL
    if match := _ENABLE.fullmatch(text) or _EXPLICIT.fullmatch(text):
        return "subscribe", (match["city"] or "").strip(), Change.ALL
    match = _WHEN.fullmatch(text) or _REVERSE.fullmatch(text)
    if not match:
        return None
    city = (match["city"] or "").strip()
    if re.search(r"不|别|不要|听说|他说|她说|假设|举例|朋友|他|她|为什么|会不会|能不能", city):
        return None
    words = match["conditions"]
    kinds = Change.RAIN if "雨" in words else Change(0)
    if re.search(r"降温|变冷|变天|温差|气温变化", words):
        kinds |= Change.COLDER
    if re.search(r"升温|变热|变天|温差|气温变化", words):
        kinds |= Change.WARMER
    return "subscribe", "" if city in {"这里", "我这里", "明天", "这边"} else city, kinds


def kind_label(kinds):
    return "、".join(label for kind, label in ((Change.RAIN, "降雨"), (Change.COLDER, "明显降温"), (Change.WARMER, "明显升温")) if kinds & kind)


def next_day_notice(report, now, kinds):
    try:
        zone = ZoneInfo(report.timezone) if report.timezone else CHINA
    except (ValueError, ZoneInfoNotFoundError):
        return None
    today = now.astimezone(zone).date()
    tomorrow = (today + timedelta(days=1)).isoformat()
    days = {d.date: d for d in report.days}
    before, after = days.get(today.isoformat()), days.get(tomorrow)
    if not before or not after:
        return None
    reasons = []
    if kinds & Change.RAIN:
        chance = after.precipitation_probability
        if _RAIN.search(after.condition):
            reasons.append(f"预报有{after.condition}，出门带把伞吧")
        elif chance is not None and 60 <= chance <= 100 and not _SNOW.search(after.condition):
            reasons.append(f"降水概率有{chance:g}%，出门可以带把伞")
    for name, old, new in (("最高温", before.high_c, after.high_c), ("最低温", before.low_c, after.low_c)):
        if old is None or new is None:
            continue
        if (new <= old - 6 and kinds & Change.COLDER) or (new >= old + 6 and kinds & Change.WARMER):
            verb = "降" if new < old else "升"
            reasons.append(f"{name}从{old:g}°C{verb}到{new:g}°C")
    if reasons:
        return tomorrow, f"{report.location}明天（{tomorrow[5:]}），" + "；".join(reasons)
    return None


def service_for(host):
    service = getattr(host, "_weather_alerts", None)
    root = getattr(getattr(host, "runtime", None), "project_root", None)
    if service is None and root is not None:
        service = host._weather_alerts = WeatherAlerts(root)
    return service


class WeatherAlerts:
    def __init__(self, root, clock=lambda: datetime.now(CHINA)):
        self.store = PlanStore(root / "data" / "weather-alerts.sqlite3", _SCHEMA)
        self.clock = clock
        self.pending = {}
        self.wake = asyncio.Event()
        self.task = None
        self.stopping = False
        self.locks = [asyncio.Lock() for _ in range(32)]

    def start(self, host):
        if self.store.path.is_file() and (self.task is None or self.task.done()):
            self.stopping = False
            self.task = asyncio.create_task(self._run(host), name="qq-weather-alerts")

    async def close(self):
        self.stopping = True
        self.wake.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.pending.clear()

    async def _run(self, host):
        while not self.stopping:
            self.wake.clear()
            try:
                await self.check(host)
            except Exception as exc:  # noqa: BLE001 -- let the next evening check recover
                _LOG.warning("天气提醒检查未完成（%s）", type(exc).__name__)
            try:
                async with asyncio.timeout(900):
                    await self.wake.wait()
            except TimeoutError:
                pass

    async def city_for(self, key):
        if not self.store.path.is_file():
            return ""
        owner = json.dumps(key)
        def read(db):
            row = db.execute("SELECT city FROM cities WHERE owner=?", (owner,)).fetchone()
            if row is None:
                row = db.execute("SELECT city FROM subscriptions WHERE owner=?", (owner,)).fetchone()
            return row["city"] if row else ""
        return await asyncio.to_thread(self.store.transaction, read)

    def ask_city(self, key, action, kinds):
        now = self.clock().timestamp()
        self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
        if len(self.pending) >= 512:
            self.pending.pop(next(iter(self.pending)))
        self.pending[key] = (now + 300, action, kinds)

    async def handle(self, host, event, text):
        key = scope(event)
        request = parse_request(text)
        pending = self.pending.get(key)
        if not request and pending and pending[0] > self.clock().timestamp():
            if re.fullmatch(_CITY, text) and not re.search(r"天气|提醒|取消|谢谢|不用|你好|算了", text):
                request = pending[1], text.strip(), pending[2]
            elif text.strip(" 。") in {"算了", "不用了", "取消"}:
                self.pending.pop(key, None)
                return "好"
        if request is None:
            return None
        async with self.locks[hash(key) % len(self.locks)]:
            action, city, kinds = request
            owner = json.dumps(key)
            self.pending.pop(key, None)
            if action == "subscribe" and not city:
                city = await self.city_for(key)
                if not city:
                    self.ask_city(key, action, kinds)
                    return "想关注哪个城市？直接告诉我城市名就行"
            if action in {"subscribe", "city"}:
                try:
                    await fetch_weather(city, host.runtime._core.settings)
                except (WeatherServiceError, TimeoutError):
                    self.ask_city(key, action, kinds)
                    return "这次没查到这个城市的预报，可以再发一次城市名"
            def update(db):
                if action == "status":
                    row = db.execute("SELECT city,kinds FROM subscriptions WHERE owner=?", (owner,)).fetchone()
                    return (f"这里关注的是{row['city']}的{kind_label(row['kinds'])}，有变化时会在北京时间傍晚提醒你" if row else "你在这里还没开天气提醒")
                if action == "cancel":
                    db.execute("DELETE FROM subscriptions WHERE owner=?", (owner,))
                    return "你在这里的天气提醒已关闭"
                if action == "forget":
                    db.execute("DELETE FROM cities WHERE owner=?", (owner,))
                    return "已清除你在这里设置的天气城市"
                if action == "city":
                    db.execute("INSERT OR REPLACE INTO cities(owner,city) VALUES (?,?)", (owner, city))
                    return f"记住了，以后你在这里问天气，我就查{city}"
                db.execute("INSERT OR REPLACE INTO subscriptions(owner,origin,city,kinds) VALUES (?,?,?,?)",
                           (owner, event.unified_msg_origin, city, int(kinds)))
                return f"好，{city}明天有{kind_label(kinds)}时，我会在北京时间傍晚到这里提醒你"
            reply = await asyncio.to_thread(self.store.turn, event, update, self.clock())
            if action == "subscribe" and reply:
                self.start(host)
                self.wake.set()
            return reply

    async def check(self, host):
        now = self.clock()
        if not 18 <= now.astimezone(CHINA).hour < 22:
            return
        if hasattr(host, "parts") and not any(p.slug == "weather" for p in host.parts()):
            return
        def rows(db):
            db.execute("DELETE FROM notices WHERE day<?", ((now.date() - timedelta(days=7)).isoformat(),))
            return db.execute("SELECT * FROM subscriptions ORDER BY city,owner").fetchall()
        reports = {}
        for row in await asyncio.to_thread(self.store.transaction, rows):
            key = json.loads(row["owner"])
            if not await scheduled_allowed(host, key, row["origin"], "weather") or not await weather_enabled(host, key):
                continue
            city = row["city"]
            if city not in reports:
                try:
                    reports[city] = await fetch_weather(city, host.runtime._core.settings)
                except (WeatherServiceError, TimeoutError):
                    reports[city] = None
            if reports[city] is None:
                continue
            now = self.clock()
            if not 18 <= now.astimezone(CHINA).hour < 22:
                return
            notice = next_day_notice(reports[city], now, row["kinds"])
            if notice is None:
                continue
            day, text = notice
            def claim(db, day=day, row=row):
                # Recheck the user's current selection after the asynchronous forecast fetch.
                return db.execute(
                    "INSERT OR IGNORE INTO notices(owner,city,day) SELECT owner,city,? FROM subscriptions "
                    "WHERE owner=? AND origin=? AND city=? AND kinds=?",
                    (day, row["owner"], row["origin"], row["city"], row["kinds"])).rowcount
            def already_sent(db, day=day, row=row):
                return db.execute("SELECT 1 FROM notices WHERE owner=? AND city=? AND day=?",
                                  (row["owner"], row["city"], day)).fetchone() is not None
            if await asyncio.to_thread(self.store.transaction, already_sent):
                continue
            api = await original_qq_client(host.context, key)
            if (api is not None and await scheduled_allowed(host, key, row["origin"], "weather")
                    and await weather_enabled(host, key) and await asyncio.to_thread(self.store.transaction, claim)):
                try:
                    await send_personal_notice(api, key, text)
                except Exception as exc:  # noqa: BLE001 -- delivery may precede a lost ACK
                    # A lost acknowledgement may follow delivery. Do not re-send the same day.
                    _LOG.warning("天气提醒未获得发送确认（%s）", type(exc).__name__)


async def weather_enabled(host, key):
    _, bot, group, user = key
    database = host.runtime._core.app.state.db
    if not group and await database.is_blocked(f"qq:private:{bot}:{user}", user):
        return False
    return await database.plugin_enabled(group or f"qq:private:{bot}:{user}", "weather")
