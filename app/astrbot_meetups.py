"""Organize a real group of players, including seats already filled outside this chat."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime

from app.services.social_plans import (
    CHINA,
    CLOCK,
    DAY,
    NUMBER,
    PlanStore,
    at,
    clock_for,
    day_for,
    number,
    scope,
)

_GAMES = {"雀魂": ("雀魂", 4), "麻将": ("麻将", 4), "三麻": ("三麻", 3), "炉石": ("炉石", 2),
          "酒馆战棋": ("酒馆战棋", 0), "我的世界": ("Minecraft", 0), "minecraft": ("Minecraft", 0),
          "mc": ("Minecraft", 0), "英雄联盟": ("英雄联盟", 5), "王者": ("王者荣耀", 5),
          "无畏契约": ("无畏契约", 5), "开黑": ("开黑", 0)}
_GAME = re.compile("|".join(_GAMES), re.IGNORECASE)
_GAP = re.compile(rf"(?:(?P<seated>{NUMBER})缺|(?:还)?[差缺])\s*(?P<gap>{NUMBER})(?:个|位|人)?")
_SIZE = re.compile(rf"({NUMBER})人(?:局|场|队|组|开黑)")
_INVITE = re.compile(r"组[个队局]|组人|约一?[场局把]|凑[个局人]|一起(?:来|打|玩|开黑)|谁(?:来|能来|有空)|有(?:人|空).{0,5}(?:来|玩|打|一起)|有人吗|招人|开黑")
_CANCEL = re.compile(r"(?:(?:那|今天|这次|这场|这局|今晚|周[一二三四五六日])+(?:就)?取消|"
                     r"取消(?:这[局场](?:开黑)?|(?:这次)?(?:组局|开黑|约局))|(?:那|这局)?(?:散了|不开了|不组了))[吧啦了]?")
_MOVE = re.compile(r"^(?:这[场局]|组局|开黑|我们|那|时间|要不)*(?:改到|换到|挪到|推迟到|提前到|改成|改为|改在|改)(.+)$")
_LEAVE = re.compile(r"^(?:那我|我|算了我)(?:这次|今天|临时|还是|就)?(?:不来|来不了|不打|不参加|退出|赶不上|没法来|先撤)[了啦吧]?$")
_JOIN = re.compile(r"^(?:算我(?:一个)?|加我(?:一个)?|带我(?:一个)?|我(?:也|能|可以)?(?:来|参加|报名|加入|补位)|我有空|我可以)[吧啦啊]?$")
_DELAY = re.compile(rf"^(?:那我|我)(?:会|要|得|可能|再)?(?:晚|迟到|晚到)\s*({NUMBER}|半)\s*(?:个)?(分钟|小时)[到来左右]*$")
_ARRIVE = re.compile(r"(?:那我|我)(?:最早|大概|大约|只能|要|可以|能|得|从|到|只有)?"
                     + CLOCK.pattern + r"(?:左右|之后|以后)?(?:才|就|都)?"
                     r"(?:到|来|行|方便|有空|可以|能来|能到|能上|能上线|能玩|上线|得走|要走|必须走|前都行|前不行)?")
_STATUS = re.compile(r"(?:这[场局桌]|我们|今晚|组局|开黑|现在|那)?(?:还[差缺](?:几|多少)(?:个人?|人|位)|人齐了吗|凑齐了吗|"
                     r"(?:几|多少)个人了|谁报名了|谁来了|最早几点(?:能[开齐])?|几点(?:开[局始]?|集合)|定了几点)")
_SCHEMA = "CREATE TABLE IF NOT EXISTS meetups (room TEXT PRIMARY KEY, day TEXT, plan TEXT NOT NULL);"


def _describe(plan):
    members = list(plan["members"].values())
    slots = plan["seats"]
    reserved = plan["reserved"]
    playing = members[:max(0, slots - reserved)] if slots else members
    waiting = members[len(playing):]
    lower = max((m["after"] for m in playing if m["after"] is not None), default=None)
    upper = min((m["until"] for m in playing if m["until"] is not None), default=None)
    missing = max(0, slots - reserved - len(playing)) if slots else None
    day = date.fromisoformat(plan["day"])
    headline = f"{day.month}月{day.day}日（周{'一二三四五六日'[day.weekday()]}）的{plan['game']}"
    count = (f"还差 {missing} 人" if missing else "人齐了") if slots else f"已有 {len(playing)} 人报名"
    result = f"{headline}，{count}。"
    if lower is not None and upper is not None and lower >= upper:
        result += "大家报的时间对不上，还要商量一个都能来的时间。"
    elif lower is not None:
        clock = f"{lower // 60:02d}:{lower % 60:02d}"
        result += f"已报名的人最早 {clock} 能到。" if missing else f"按已报的时间，最早 {clock} 能开。"
        if upper is not None:
            result += f"要在 {upper // 60:02d}:{upper % 60:02d} 前开。"
    else:
        result += "开局时间待定。"
    result += " 报名：" + "、".join(m["name"] for m in playing) + "。"
    if reserved:
        result += f" 发起时另有 {reserved} 人已占位。"
    if waiting:
        result += " 候补：" + "、".join(m["name"] for m in waiting) + "。"
    return result


def _minutes(value):
    return value.hour * 60 + value.minute if value is not None else None


class Meetups:
    def __init__(self, root, clock=lambda: datetime.now(CHINA)):
        self.store = PlanStore(root / "data" / "meetups.sqlite3", _SCHEMA)
        self.clock = clock

    async def handle(self, event, text):
        platform, bot, group, user = scope(event)
        if not group:
            return None
        text = text.strip(" ，。！？!?～~")
        clauses = re.split(r"[，,]\s*", text, maxsplit=1)
        if len(clauses) == 2 and _JOIN.fullmatch(clauses[0]):
            arrival_text = clauses[1] if clauses[1].startswith("我") else "我" + clauses[1]
            if _ARRIVE.fullmatch(arrival_text) or _DELAY.fullmatch(arrival_text):
                text = arrival_text
        if re.search(r"^(?:听说|比如|例如|他说|她说|你说|如果|假如|[>“「])|(?:别|不想|不打算|不打|不要|不玩).{0,4}(?:组局|开黑|雀魂|麻将|炉石)", text):
            return None
        game = _GAME.search(text)
        gap, size = _GAP.search(text), _SIZE.search(text)
        start = game and (gap or DAY.search(text) and (_INVITE.search(text) or size))
        move, delay = _MOVE.fullmatch(text), _DELAY.fullmatch(text)
        arrival = _ARRIVE.fullmatch(text)
        cancel, leave, join, status = (_CANCEL.fullmatch(text), _LEAVE.fullmatch(text), _JOIN.fullmatch(text), _STATUS.fullmatch(text))
        if not (start or move or delay or arrival or cancel or leave or join or status):
            return None
        if not start and not self.store.path.exists():
            return None
        now = self.clock()
        room = json.dumps([platform, bot, group])
        sender = event.message_obj.raw_message.get("sender") or {}
        name = re.sub(r"[\r\n]", "", str(sender.get("card") or sender.get("nickname") or user))[:36]

        def update(db):
            db.execute("DELETE FROM meetups WHERE day < ?", (now.date().isoformat(),))
            row = db.execute("SELECT plan FROM meetups WHERE room=?", (room,)).fetchone()
            plan = json.loads(row["plan"]) if row else None
            prefix = ""
            if start:
                if plan:
                    return "群里已经约了一场。" + _describe(plan)
                day = day_for(text, now)
                when = clock_for(text, evening=True)
                if day < now.date() or when and at(day, when) <= now:
                    return "这个开局时间已经过了，想约哪天？"
                game_name, seats = _GAMES[game[0].lower()]
                if "三麻" in text:
                    game_name, seats = "三麻", 3
                if size:
                    seats = number(size[1])
                missing = number(gap["gap"]) if gap else None
                if gap and gap["seated"]:
                    seats = number(gap["seated"]) + missing
                if missing is not None and not seats:
                    seats = 1 + missing
                if not 0 <= seats <= 40 or missing is not None and not 0 < missing < seats:
                    return "这局一共几人、现在差几人？可以说「周六四人局打雀魂，还差两人」。"
                plan = {"day": day.isoformat(), "game": game_name, "owner": user, "seats": seats,
                        "reserved": max(0, seats - missing - 1) if missing is not None else 0,
                        "evening": when.hour >= 12 if when else not re.search(r"早|上午|中午", text),
                        "members": {user: {"name": name, "after": _minutes(when), "until": None}}}
            elif not plan:
                return None
            elif cancel or move:
                if user != plan["owner"]:
                    return "改整场的时间或取消要发起人来说。你可以直接报自己的时间，或者说「我不来了」。"
                if cancel:
                    db.execute("DELETE FROM meetups WHERE room=?", (room,))
                    return f"这场{plan['game']}取消了。"
                if not (DAY.search(move[1]) or CLOCK.search(move[1])):
                    return None
                day = day_for(move[1], now, date.fromisoformat(plan["day"]))
                when = clock_for(move[1], evening=plan.get("evening", True))
                if day < now.date() or when and at(day, when) <= now:
                    return "这个时间已经过去了，换个时间吧。"
                owner = plan["members"][user]
                if day.isoformat() != plan["day"]:
                    plan.update(day=day.isoformat(), reserved=0, members={user: owner})
                    owner.update(after=None, until=None)
                    prefix = "换日了，原来报名的人请再确认一次。 "
                if when:
                    owner.update(after=_minutes(when), until=None)
                    plan["evening"] = when.hour >= 12
            elif status:
                return _describe(plan)
            elif leave:
                if user not in plan["members"]:
                    return None
                del plan["members"][user]
                if user == plan["owner"]:
                    if not plan["members"]:
                        db.execute("DELETE FROM meetups WHERE room=?", (room,))
                        return "你退出了，群里没有已报名的玩家接手，这场约局就结束了。"
                    plan["owner"] = next(iter(plan["members"]))
                    prefix = f"你退出了，由{plan['members'][plan['owner']]['name']}接着组织。 "
            else:
                member = plan["members"].get(user, {"name": name, "after": None, "until": None})
                if delay:
                    if delay[1] == "半":
                        n = 30 if delay[2] == "小时" else 0
                    else:
                        n = number(delay[1]) * (60 if delay[2] == "小时" else 1)
                    baseline = member["after"]
                    if baseline is None:
                        baseline = max((m["after"] for m in plan["members"].values() if m["after"] is not None), default=None)
                    if baseline is None or n <= 0 or baseline + n >= 1440:
                        return "你大概几点能到？告诉我钟点，我把你的时间算进去。"
                    member["after"] = baseline + n
                if arrival:
                    when = clock_for(text, evening=plan.get("evening", True))
                    member["until" if any(s in text for s in ("要走", "得走", "必须走", "前都行")) else "after"] = _minutes(when)
                plan["members"][user] = member
            db.execute("INSERT OR REPLACE INTO meetups VALUES (?,?,?)", (room, plan["day"], json.dumps(plan, ensure_ascii=False)))
            return prefix + _describe(plan)

        try:
            return await asyncio.to_thread(self.store.turn, event, update, now)
        except ValueError:
            return "没读准这个钟点，能用「晚上八点半」或「20:30」再说一次吗？"
