"""Personal quiet study sessions with pause/resume and an opt-in end reminder."""
import asyncio
import json
import math
import re

from app.services.activity_timer import ActivityTimer
from app.services.social_plans import (
    NUMBER,
    number,
    original_qq_client,
    scheduled_allowed,
    send_personal_notice,
)

_START = re.compile(r"(?:陪我|我们一起|开始|开个|我要|来个|我想)(?:安静)?(?:自习|专注|学习|番茄钟)(?:一下|一会儿|一会|计时)?[，,：: ]*"
                    rf"(?:(半(?:个)?小时)|({NUMBER})\s*(分钟|分|个小时|小时))?(?:[，,：: ]*(?:做|写|复习|看|学|目标是)?(.{{1,90}}))?$")
_CONTROL = re.compile(r"^(暂停|继续|恢复|取消|结束|完成|查看|看看)(?:我的|这次)?(?:自习|学习计时|专注|番茄钟)(?:计时|状态|了)?$|^(?:自习|专注)(还剩(?:多久|几分钟)|状态)$")


class Study(ActivityTimer):
    slug = "study"
    title = "安静自习"
    schema = """CREATE TABLE IF NOT EXISTS sessions (owner TEXT PRIMARY KEY, origin TEXT NOT NULL, topic TEXT NOT NULL,
         due REAL NOT NULL, remaining INTEGER NOT NULL, state TEXT NOT NULL);"""

    def interested(self,key,text,addressed):
        value = text.strip(" 。！？!?")
        return bool((addressed or not key[2]) and (_START.fullmatch(value) or _CONTROL.fullmatch(value)))

    async def handle(self,host,event,key,text):
        text = text.strip(" 。！？!?")
        start, action = _START.fullmatch(text), _CONTROL.fullmatch(text)
        if not (start or action): return False
        owner, now = json.dumps(key), self.clock()
        store = self.store(host)
        def change(db):
            row = db.execute("SELECT * FROM sessions WHERE owner=?", (owner,)).fetchone()
            if start:
                if row and row["state"] in {"running","paused"} and (row["state"] == "paused" or row["due"] > now.timestamp()):
                    return "上次自习还在，可以问「自习还剩多久」或说「结束自习」。"
                duration = 30 if start[1] else number(start[2]) * (60 if "小时" in start[3] else 1) if start[2] else 25
                if not 1 <= duration <= 1440: return "你想专注几分钟？可以设 1 分钟到 24 小时。"
                topic = (start[4] or "自习").strip("，。 ")
                db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,'running')",
                           (owner,event.unified_msg_origin,topic,now.timestamp()+duration*60,duration*60))
                return f"好，陪你专注{duration}分钟。到点叫你，期间有问题直接问我。"
            if not row: return "这里还没有你的自习计时。"
            verb = action[1] or "查看"
            remaining = row["remaining"] if row["state"] == "paused" else max(0,math.ceil(row["due"]-now.timestamp()))
            if verb in {"取消","完成","结束"}:
                db.execute("DELETE FROM sessions WHERE owner=?", (owner,))
                return "这次自习已结束。" if verb != "取消" else "计时已取消。"
            if verb == "暂停" and row["state"] == "running" and remaining:
                db.execute("UPDATE sessions SET state='paused',remaining=? WHERE owner=?", (remaining,owner))
                return f"计时已暂停，还剩{math.ceil(remaining/60)}分钟。"
            if verb in {"继续","恢复"} and row["state"] == "paused":
                db.execute("UPDATE sessions SET state='running',due=? WHERE owner=?", (now.timestamp()+remaining,owner))
                return f"继续计时，还剩{math.ceil(remaining/60)}分钟。"
            if row["state"] == "finished" or not remaining:
                return "这次自习时间到了。"
            status = "已暂停" if row["state"] == "paused" else "正在进行"
            return f"「{row['topic']}」{status}，还剩{math.ceil(remaining/60)}分钟。"
        reply = await asyncio.to_thread(store.turn,event,change,now)
        self.start(host)
        if reply: await event.send(event.plain_result(reply))
        return True

    async def check_due(self,host):
        store, now = self.store(host), self.clock().timestamp()
        rows = await asyncio.to_thread(store.transaction,lambda db: db.execute(
            "SELECT * FROM sessions WHERE state='running' AND due<=?",(now,)).fetchall())
        for row in rows:
            key = json.loads(row["owner"])
            if not await scheduled_allowed(host,key,row["origin"],self.slug): continue
            api = await original_qq_client(host.context,key)
            if not api: continue
            claim = await asyncio.to_thread(store.transaction,lambda db, row=row: db.execute(
                "UPDATE sessions SET state='finished' WHERE owner=? AND state='running' AND due=?",(row["owner"],row["due"])).rowcount)
            if claim:
                await send_personal_notice(api,key,f"「{row['topic']}」的自习时间到了，休息一下吧。")

    async def quiet(self,host,origin):
        store = self.store(host)
        if not store.path.exists():
            return False
        return bool(await asyncio.to_thread(store.transaction,lambda db: db.execute(
            "SELECT 1 FROM sessions WHERE origin=? AND state='running' AND due>? LIMIT 1",(origin,self.clock().timestamp())).fetchone()))
