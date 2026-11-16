"""Ask once about a personal milestone, on the original QQ account and in the original conversation."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
from datetime import datetime, time, timedelta

from app.services.social_plans import (
    CHINA,
    CLOCK,
    DAY,
    PlanStore,
    at,
    clock_for,
    day_for,
    label,
    original_qq_client,
    scheduled_allowed,
    scope,
    send_personal_notice,
)

_LOG = logging.getLogger(__name__)
_TOPIC = re.compile(r"面试|考试|答辩|比赛|演出|汇报|路演|试讲|考驾照")
_PERSONAL = re.compile(r"^(?:我|今天我|明天我|后天我|今晚我|明晚我|(?:下|这)?周[一二三四五六日]我)")
_OTHER = re.compile(r"我(?:的)?(?:朋友|家人|室友|同学|同事|弟弟|妹妹|哥哥|姐姐|对象|妈妈|爸爸)|我(?:听说|记得他说|帮|陪|听|带他|带她)|别人|不用问|不(?:去|打算|参加|想去)|(?:不|没|没有)(?:面试|考试|答辩|比赛|演出)|取消了|好像|如果|假如|比如|例如|教程")
_ASK = re.compile(r"(?:记得|到时候|结束后|之后|再|来)?问(?:一?下|一问|问)?我")
_YES = re.compile(r"(?:好(?:的|呀|啊|吧|哇)?|可以(?:呀|啊)?|行(?:啊|呀)?|嗯嗯|同意|要的|要)[，, ]*")
_NO = re.compile(r"(?:不(?:用|要|必)(?:了|啦)?|算了|先不用|不了)")
_STOP = re.compile(r"(?:算了[，, ]*)?(?:别|不用|不要)(?:再|到时候)?问(?:我|了|啦)*|取消(?:这次|我的|这个)?回访")
_MOVE = re.compile(r"(?:把)?(?:回访|问我(?:的)?时间|再问我的时间)(?:时间)?(?:改|挪|换|推迟|提前)(?:到|成|在)?")
_WHEN = re.compile(r"(?:好(?:的)?|可以|那就|那|改到|换到)?[，, ]*(?:今天|明天|后天|今晚|明晚|明早|(?:下|这|本)?周[一二三四五六日天])?"
                   + CLOCK.pattern + r"(?:吧|问我(?:吧)?|再问我(?:吧)?|来问我(?:吧)?)?")
_SCHEMA = """CREATE TABLE IF NOT EXISTS followups
    (id INTEGER PRIMARY KEY, owner TEXT NOT NULL, topic TEXT NOT NULL, origin TEXT NOT NULL,
     due INTEGER NOT NULL, expires INTEGER NOT NULL, state TEXT NOT NULL, UNIQUE(owner, topic));
    CREATE INDEX IF NOT EXISTS followup_due ON followups(state,due);"""


def _request_clause(text):
    for clause in re.split(r"[，,。；;！!？?]", text):
        ask = _ASK.search(clause)
        if not ask:
            continue
        prefix = CLOCK.sub("", DAY.sub("", clause[:ask.start()]))
        prefix = re.sub(r"(?:面试|考试|答辩|比赛|演出|汇报|路演|试讲)(?:结束|完)后", "", prefix)
        if re.fullmatch(r"(?:你|请|麻烦|能不能|可以|记得|帮我|到时(?:候)?|结束后|过后|再|来|在这里|在|晚上|上午|中午|下午|早上|\s)*", prefix):
            return clause
    return ""


def _suggest(text, now, request):
    day = day_for(request, now, day_for(text, now)) if request else day_for(text, now)
    text = request or text
    moment = clock_for(text)
    if moment:
        when = at(day, moment)
        return when if request or when <= now else when + timedelta(hours=2)
    periods = (("早", 12), ("上午", 12), ("中午", 16), ("晚", 21))
    hour = next((h for word, h in periods if word in text), 19)
    return at(day, time(hour))


class Followups:
    def __init__(self, root, clock=lambda: datetime.now(CHINA)):
        self.store = PlanStore(root / "data" / "followups.sqlite3", _SCHEMA)
        self.clock = clock
        self.previous_offer = OrderedDict()
        self.task = None
        self.wake = asyncio.Event()
        self.stopping = False

    async def handle(self, host, event, text, *, addressed=False):
        key = scope(event)
        owner = json.dumps(key)
        room = key[:3]
        previous = self.previous_offer.pop(room, None) if key[2] else None
        text = text.strip(" ，。！？!?～~")
        if re.search(r"^(?:[>“「]|他说|她说|听说|据说|举例|假设)|(?:提醒我|陪我练|模拟面试)", text):
            return None
        topic = _TOPIC.search(text)
        ask = _request_clause(text) if not _STOP.search(text) else ""
        personal = (topic and DAY.search(text) and _PERSONAL.search(text) and not _OTHER.search(text)
                    and not re.search(r"怎么|几点|什么时候|攻略|帮我|陪我|模拟|练习|会问|问我什么|[？?]", text))
        explicit = (topic and ask and (DAY.search(text) or CLOCK.search(ask)) and not _OTHER.search(text)
                    and not re.search(r"(?:别|不要|不想|不用|别让).{0,16}问", text))
        cancel = _STOP.fullmatch(text) or (topic and _STOP.search(text))
        move = _MOVE.search(text) or (topic and ask and re.search(r"改到|换到|挪到", text))
        yes = _YES.fullmatch(text) or re.fullmatch(r"(?:好[，, ]*)?(?:结束后|到时候|到时|之后)?(?:再|来)?问问?我(?:吧|就好)?", text)
        revised, no = _WHEN.fullmatch(text), _NO.fullmatch(text)
        if not (personal or explicit or cancel or move or yes or revised or no):
            return None
        if not (personal or explicit) and not self.store.path.exists():
            return None
        now = self.clock()
        stamp = int(now.timestamp())
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if not origin:
            return None

        def update(db):
            db.execute("DELETE FROM followups WHERE state='offer' AND expires<=?", (stamp,))
            rows = db.execute("SELECT * FROM followups WHERE owner=? ORDER BY id DESC", (owner,)).fetchall()
            selected = [row for row in rows if not topic or row["topic"] == topic[0]]
            offers = [row for row in selected if row["state"] == "offer"]
            if cancel or no:
                targets = selected if cancel else offers
                if not targets:
                    return None
                if len(targets) > 1:
                    return "想取消哪一件？可以说「面试不用问我了」。"
                db.execute("DELETE FROM followups WHERE id=?", (targets[0]["id"],))
                return f"好，{targets[0]['topic']}这件事就不再问了。"
            if move and selected:
                if len(selected) != 1:
                    return "你想改哪一件的时间？可以说「面试回访改到明晚八点」。"
                row = selected[0]
            elif personal or explicit:
                when = _suggest(text, now, ask if explicit else "")
                if when <= now:
                    return "想让我什么时候来问？可以说「明晚八点问我面试怎么样」。" if explicit else None
                enrolled = next((row for row in selected if row["state"] == "confirmed"), None)
                if enrolled and not explicit:
                    return None
                state = "confirmed" if explicit and CLOCK.search(ask) else "offer"
                db.execute("""INSERT INTO followups(owner,topic,origin,due,expires,state) VALUES (?,?,?,?,?,?)
                              ON CONFLICT(owner,topic) DO UPDATE SET origin=excluded.origin,due=excluded.due,
                              expires=excluded.expires,state=excluded.state""",
                           (owner, topic[0], origin, int(when.timestamp()), stamp + 600, state))
                if state == "confirmed":
                    return f"好，{label(when)} 我来这里问你{topic[0]}怎么样。"
                return f"{topic[0]}结束后，要我在{label(when)}来这里问问你怎么样吗？也可以换个时间。"
            elif (yes or revised) and offers:
                # A bare group “好” can be for someone else. Accept it following our offer or addressed to us.
                if key[2] and not addressed and previous != key[3] and not ask:
                    return None
                if len(offers) > 1:
                    return "你想让我问哪一件？告诉我时间和事情，比如「明晚八点问我面试怎么样」。"
                row = offers[0]
            else:
                return None
            original = datetime.fromtimestamp(row["due"], CHINA)
            moment = clock_for(text, evening=original.hour >= 12)
            if move and not (moment or DAY.search(text)):
                return "想换到哪天几点？可以说「回访改到明晚八点」。"
            when = at(day_for(text, now, original.date()), moment or original.time())
            if when <= now:
                return "这个钟点已经过了，想换到什么时候？"
            db.execute("UPDATE followups SET due=?, state='confirmed' WHERE id=?", (int(when.timestamp()), row["id"]))
            return f"好，{label(when)} 我来这里问你{row['topic']}怎么样。"

        try:
            reply = await asyncio.to_thread(self.store.turn, event, update, now)
        except ValueError:
            return "这个钟点我没读准。可以说「明晚八点问我面试怎么样」。"
        if reply and "也可以换个时间" in reply and key[2]:
            self.previous_offer[room] = key[3]
            if len(self.previous_offer) > 256:
                self.previous_offer.popitem(last=False)
        if reply:
            self.start(host)
            self.wake.set()
        return reply

    def start(self, host):
        if self.store.path.is_file() and (self.task is None or self.task.done()):
            self.stopping = False
            self.task = asyncio.create_task(self._run(host))

    async def close(self):
        self.stopping = True
        self.wake.set()
        if self.task is not None:
            await self.task
            self.task = None

    async def _run(self, host):
        while not self.stopping:
            self.wake.clear()
            try:
                await self.deliver_due(host)
            except Exception as exc:  # noqa: BLE001 -- a failed tick must not terminate the scheduler.
                _LOG.warning("读取约定回访失败（%s）", type(exc).__name__)
            if not self.stopping:
                try:
                    await asyncio.wait_for(self.wake.wait(), 20)
                except TimeoutError:
                    pass

    async def deliver_due(self, host):
        stamp = int(self.clock().timestamp())
        def due(db):
            db.execute("DELETE FROM followups WHERE state='offer' AND expires<=?", (stamp,))
            return db.execute("SELECT * FROM followups WHERE state='confirmed' AND due<=? ORDER BY due,id", (stamp,)).fetchall()

        rows = await asyncio.to_thread(self.store.transaction, due)
        for row in rows:
            if self.stopping:
                break
            key = json.loads(row["owner"])
            allowed = await scheduled_allowed(host, key, row["origin"], "followups")
            if hasattr(host, "parts") and not any(part.slug == "followups" for part in host.parts()):
                return
            api = await original_qq_client(host.context, key) if allowed else None
            if allowed and api is None:
                continue

            def claim(db, job=row):
                return db.execute("DELETE FROM followups WHERE id=? AND owner=? AND state='confirmed' AND due=?",
                                  (job["id"], job["owner"], job["due"])).rowcount

            if self.stopping or not await asyncio.to_thread(self.store.transaction, claim) or not allowed:
                continue
            try:
                await send_personal_notice(api, key, f"上次你说的{row['topic']}，后来怎么样了？")
            except Exception as exc:  # noqa: BLE001 -- a transport error can follow delivery.
                # NapCat can accept a send before losing its acknowledgement. Never repeat an uncertain send.
                _LOG.warning("约定回访未获发送确认（%s）", type(exc).__name__)
