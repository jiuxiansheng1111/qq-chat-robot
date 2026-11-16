"""Group time capsules with per-author contributions, a Beijing opening date and durable delivery claims."""
from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import date, datetime, time

from app.services.activity_notes import attached_photo, attached_voice
from app.services.activity_timer import ActivityTimer
from app.services.social_plans import (
    CHINA,
    DAY,
    clock_for,
    day_for,
    label,
    original_qq_client,
    scheduled_allowed,
)

_CREATE = re.compile(r"^(?:建(?:立|个|一个)?|开(?:个|一个)?|创建)(?:群周年|群)?时间胶囊[，,：: ]*(.*)$")
_ADD = re.compile(r"^(?:往|给)?(?:群周年|群)?时间胶囊(?:里)?(?:放(?:入|进)?|存(?:入|进)?|写(?:入)?|留言)[一条段张份个]*[，,：: ]*(.*)$",re.DOTALL)
_ACT = re.compile(r"^(?:(查看|打开|取消|撤回|取回)(?:我的)?(?:群周年|群)?时间胶囊(?:投稿|内容|状态)?|撤回我的胶囊投稿|胶囊什么时候开)$")
_DATE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?")


def opening_time(text, now):
    explicit = _DATE.search(text)
    if explicit:
        target = date(*(int(part) for part in explicit.groups()))
    elif DAY.search(text):
        target = day_for(text, now)
    else:
        raise ValueError("说一下开封日期和时间，例如「建个群时间胶囊 2027-06-01 20:00」。")
    target_time = clock_for(text) or time(9)
    instant = datetime.combine(target,target_time,CHINA)
    if instant <= now:
        raise ValueError("开封时间要在以后，按北京时间算。")
    return instant


class TimeCapsule(ActivityTimer):
    title = "群周年时间胶囊"
    slug = "time_capsule"
    schema = """CREATE TABLE IF NOT EXISTS capsules(room TEXT PRIMARY KEY,owner TEXT NOT NULL,origin TEXT NOT NULL,due REAL NOT NULL,
                                       state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS letters(id INTEGER PRIMARY KEY,room TEXT NOT NULL,author TEXT NOT NULL,nick TEXT NOT NULL,
             content TEXT NOT NULL, photo BLOB, voice BLOB, sent INTEGER NOT NULL DEFAULT 0, voice_sent INTEGER NOT NULL DEFAULT 0);"""

    def interested(self,key,text,addressed):
        return bool(key[2] and any(p.fullmatch(text.strip(" 。？?！!")) for p in (_CREATE,_ADD,_ACT)))

    async def handle(self,host,event,key,text):
        if not key[2]: return False
        text = text.strip(" 。？?！!")
        create, add, act = _CREATE.fullmatch(text), _ADD.fullmatch(text), _ACT.fullmatch(text)
        if not any((create,add,act)): return False
        store, now, room = self.store(host), self.clock(), json.dumps(key[:3])
        photo = voice = None
        if add:
            existing = store.transaction(lambda db: db.execute("SELECT due,state FROM capsules WHERE room=?", (room,)).fetchone())
            if existing and existing["state"] == "sealed" and now.timestamp() < existing["due"]:
                photo = await attached_photo(event)
                voice = await attached_voice(host,event)
        def change(db):
            row = db.execute("SELECT * FROM capsules WHERE room=?", (room,)).fetchone()
            if create:
                if row and row["state"] != "opened": return "本群已经有一只时间胶囊了。"
                if row:
                    # An opened capsule can be replaced only after the scheduled contributions have finished.
                    pending = db.execute("SELECT 1 FROM letters WHERE room=? AND (sent=0 OR (voice IS NOT NULL AND voice_sent=0))",(room,)).fetchone()
                    if pending: return "上一只胶囊还在送出投稿，送完就能新建。"
                when = opening_time(create[1], now)
                db.execute("DELETE FROM letters WHERE room=?", (room,))
                db.execute("INSERT OR REPLACE INTO capsules VALUES (?,?,?,?,'sealed')",(room,json.dumps(key),event.unified_msg_origin,when.timestamp()))
                return f"这只胶囊会在北京时间{label(when)}打开。愿意参加的人可以说「时间胶囊里放：……」，也可以附照片或语音。"
            if not row: return "本群还没有时间胶囊。"
            due = datetime.fromtimestamp(row["due"],CHINA)
            if add:
                if row["state"] != "sealed" or now.timestamp() >= row["due"]: return "已经到开封时间，收件结束了。"
                content = add[1].strip()
                if not (content or photo or voice): return "想放一句话、一张照片，还是一段语音？随投稿附上就可以。"
                if len(content)>1500: return "一条留言可以放 1500 字以内。"
                sender = event.message_obj.raw_message.get("sender") or {}
                nick = str(sender.get("card") or sender.get("nickname") or "群友")[:48]
                db.execute("INSERT INTO letters(room,author,nick,content,photo,voice) VALUES (?,?,?,?,?,?)",
                           (room,key[-1],nick,content,photo,voice))
                return "你的投稿已经封好。开封前可以说「撤回我的胶囊投稿」。"
            verb = act[1] or ("撤回" if "撤回" in text else "查看")
            if verb == "取消":
                count = db.execute("SELECT COUNT(*) FROM letters WHERE room=?",(room,)).fetchone()[0]
                if json.loads(row["owner"])[-1]!=key[-1] or count: return "只有发起人能取消空胶囊；每个人可以自行撤回投稿。"
                db.execute("DELETE FROM capsules WHERE room=?",(room,))
                return "空胶囊已收起。"
            if verb in {"撤回","取回"}:
                if row["state"]!="sealed" or now.timestamp()>=row["due"]: return "开封时间已到，收件已经结束。"
                n = db.execute("DELETE FROM letters WHERE room=? AND author=?",(room,key[-1])).rowcount
                return f"已撤回你自己的{n}条投稿。"
            count = db.execute("SELECT COUNT(*) FROM letters WHERE room=?",(room,)).fetchone()[0]
            if row["state"] == "opened": return f"本群胶囊已开封，共{count}条投稿。"
            return f"共{count}条投稿，开封时间是北京时间{label(due)}。" if now.timestamp()<row["due"] else "开封时间到了，正在原群送出投稿。"
        try: reply = await asyncio.to_thread(store.turn,event,change,now)
        except ValueError as exc: reply = str(exc)
        self.start(host)
        if reply: await event.send(event.plain_result(reply))
        return True

    async def check_due(self,host):
        store = self.store(host)
        def due(db):
            return db.execute("""SELECT * FROM capsules c WHERE due<=? AND (state='sealed' OR EXISTS
                (SELECT 1 FROM letters l WHERE l.room=c.room AND (sent=0 OR (voice IS NOT NULL AND voice_sent=0))))""",
                (self.clock().timestamp(),)).fetchall()
        for capsule in await asyncio.to_thread(store.transaction,due):
            key,room = json.loads(capsule["owner"]),capsule["room"]
            if not await scheduled_allowed(host,key,capsule["origin"],self.slug): continue
            api = await original_qq_client(host.context,key)
            if not api: continue
            async def send(segments, api=api, key=key):
                await asyncio.wait_for(api.call_action("send_group_msg", self_id=key[1], group_id=key[2], message=segments),15)
            claimed = store.transaction(lambda db, room=room, capsule=capsule: db.execute("UPDATE capsules SET state='opened' WHERE room=? AND state='sealed' AND due=?",
                                                              (room,capsule["due"])).rowcount)
            if claimed:
                await send([{"type":"text","data":{"text":"到约好的时间了，群时间胶囊开封。"}}])
            # Load one attachment at a time; a group can leave many large photos.
            ids = store.transaction(lambda db, room=room: db.execute("SELECT id FROM letters WHERE room=? AND (sent=0 OR (voice IS NOT NULL AND voice_sent=0)) ORDER BY id",(room,)).fetchall())
            for entry in ids:
                def take(db, column, entry=entry, room=room):
                    if not db.execute("UPDATE letters SET "+column+"=1 WHERE id=? AND room=? AND "+column+"=0",(entry[0],room)).rowcount:
                        return None
                    return db.execute("SELECT nick,content,photo FROM letters WHERE id=?" if column=="sent" else "SELECT voice FROM letters WHERE id=?", (entry[0],)).fetchone()
                part = await asyncio.to_thread(store.transaction,lambda db: take(db,"sent"))
                if part:
                    segments = [{"type":"text","data":{"text":part["nick"]+" 留下的："+part["content"]}}]
                    if part["photo"]:
                        segments.append({"type":"image","data":{"file":"base64://"+base64.b64encode(part["photo"]).decode()}})
                    await send(segments)
                sound = await asyncio.to_thread(store.transaction,lambda db: take(db,"voice_sent"))
                if sound and sound[0]:
                    await send([{"type":"record","data":{"file":"base64://"+base64.b64encode(sound[0]).decode()}}])
