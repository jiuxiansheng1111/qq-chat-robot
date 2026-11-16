"""Shared civil-time parsing and transactional storage for QQ social plans."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, time, timedelta, timezone

CHINA = timezone(timedelta(hours=8))
NUMBER = r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})"
DAY = re.compile(r"大后天|后天|明天|明早|明晚|今早|今天|今晚|今夜|(?:(?:下|本|这)(?:个)?)?(?:星期|礼拜|周)[一二三四五六日天末]")
CLOCK = re.compile(rf"(?P<period>凌晨|清晨|早上|早晨|上午|中午|下午|傍晚|晚上|晚间|今晚|明晚|明早|晚|早)?\s*"
                   rf"(?P<hour>{NUMBER})(?:[：:](?P<min>\d{{2}})|[点时](?:(?P<half>半)|(?P<quarter>[一三])刻|(?P<minute>{NUMBER})分?)?)")


def number(value):
    if value.isdecimal():
        return int(value)
    digits = "零一二三四五六七八九"
    value = value.replace("两", "二").replace("〇", "零")
    if "十" in value:
        tens, _, units = value.partition("十")
        if len(tens) <= 1 and len(units) <= 1 and all(c in digits for c in tens + units):
            return (digits.index(tens) if tens else 1) * 10 + (digits.index(units) if units else 0)
        return -1
    return int("".join(str(digits.index(c)) for c in value))


def day_for(text, now, default=None):
    match = DAY.search(text)
    today = now.date()
    if not match:
        return default or today
    word = match[0]
    if word in {"今天", "今晚", "今早", "今夜", "明天", "明晚", "明早", "后天", "大后天"}:
        return today + timedelta(days=3 if word == "大后天" else 2 if word == "后天" else int(word[0] == "明"))
    weekday = "一二三四五六日".index(word[-1].replace("天", "日").replace("末", "六"))
    delta = weekday - today.weekday()
    if word.startswith("下"):
        delta += 7
    elif not word.startswith(("这", "本")):
        delta %= 7
    return today + timedelta(days=delta)


def clock_for(text, *, evening=False):
    match = CLOCK.search(text)
    if not match:
        return None
    h = number(match["hour"])
    minute = match["min"] or match["minute"]
    m = number(minute) if minute else 30 if match["half"] else {"一": 15, "三": 45}.get(match["quarter"], 0)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("invalid clock")
    period = match["period"] or ""
    if period in {"凌晨", "早", "明早", "清晨", "早上", "早晨", "上午"} and h == 12:
        h = 0
    elif h < 12 and (period in {"下午", "晚上", "傍晚", "晚间", "今晚", "明晚", "晚"}
                     or period == "中午" and h <= 5 or not period and evening and 1 <= h <= 11 and not match["min"]):
        h += 12
    return time(h, m)


def at(day, clock):
    return datetime.combine(day, clock, CHINA)


def label(when):
    return f"{when.month}月{when.day}日（周{'一二三四五六日'[when.weekday()]}）{when:%H:%M}"


def scope(event):
    raw = event.message_obj.raw_message
    group = str(raw.get("group_id") or "") if raw.get("message_type") == "group" else ""
    return (str(event.get_platform_id()), str(raw["self_id"]), group, str(raw["user_id"]))


class PlanStore:
    def __init__(self, path, schema):
        self.path = path
        self.schema = schema

    def transaction(self, operation, *, receipt=None, now=0):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=8)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(self.schema)
            db.execute("CREATE TABLE IF NOT EXISTS receipts (key TEXT PRIMARY KEY, at INTEGER NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            if receipt and db.execute("SELECT 1 FROM receipts WHERE key=?", (receipt,)).fetchone():
                return ""
            reply = operation(db)
            if receipt and reply is not None:
                db.execute("DELETE FROM receipts WHERE at<?", (now - 172800,))
                db.execute("INSERT INTO receipts VALUES (?,?)", (receipt, now))
            return reply

    def turn(self, event, operation, now):
        ident = event.message_obj.raw_message.get("message_id")
        key = json.dumps([*scope(event), str(ident)]) if ident else None
        return self.transaction(operation, receipt=key, now=now.timestamp())
