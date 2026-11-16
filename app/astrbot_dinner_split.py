"""Shared dinner ledger with explicit payers, item participants and exact-cent settlement."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from decimal import Decimal

from app.astrbot_compare import image_refs, parse_object
from app.astrbot_turn_features import observe_image
from app.services.social_plans import CHINA, PlanStore

_OPEN = re.compile(r"^(?:开个|开始|开一个|帮我们算下|帮我算下)?(?:聚餐AA|饭钱AA|AA账本)(?:[，,：: ]*(?:参加的人是|参加人|参与人|我们有|有)?[：: ]*(.*))?$", re.IGNORECASE)
_ADD = re.compile(r"^(?:AA记一笔|聚餐记一笔|给AA记一笔|记一笔)[，,：: ]*(.{1,24}?)(?:先)?(?:付了?|垫了?|买单)[：: ]*(\d+(?:\.\d{1,2})?)(?:元|块)(.*)$", re.IGNORECASE)
_END = re.compile(r"^(?:聚餐AA|AA)(?:结算|账单|怎么算|算一下|算账|明细)$", re.IGNORECASE)
_UNDO = re.compile(r"^(?:撤销|删掉|删除)(?:上笔AA|最后一笔AA|我上笔AA|AA第(\d+)笔)$", re.IGNORECASE)
_PHOTO = re.compile(r"^(?:帮我|请)?(?:把|将)?(?:这张|这份)?(?:小票|账单)(?:记入|加到|算到|录入)AA[账本里]*$", re.IGNORECASE)
_YES = re.compile(r"^(?:确认|录入|取消)(?:这张)?(?:AA)?小票$", re.IGNORECASE)
_CLOSE = re.compile(r"^(?:关闭|清空|结束)(?:这次)?(?:聚餐)?AA账本$", re.IGNORECASE)
_SCHEMA = """CREATE TABLE IF NOT EXISTS bills (room TEXT PRIMARY KEY, owner TEXT NOT NULL, people TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS expenses (id INTEGER PRIMARY KEY,room TEXT NOT NULL, actor TEXT NOT NULL, payer TEXT NOT NULL,
 name TEXT NOT NULL, amount INTEGER NOT NULL, people TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drafts (room TEXT NOT NULL, actor TEXT NOT NULL, rows TEXT NOT NULL, PRIMARY KEY(room,actor));"""


def cents(amount):
    word = str(amount)
    if not re.fullmatch(r"\d{1,8}(?:\.\d{1,2})?", word) or Decimal(word) <= 0:
        raise ValueError("金额要大于零，最多保留两位小数。")
    return int(Decimal(word) * 100)


def money(value):
    return f"{value // 100}.{value % 100:02d}"


def settle(people, rows):
    balances = dict.fromkeys(people, 0)
    for payer, amount, members in rows:
        members = [name for name in people if name in members]
        if not members or payer not in balances:
            raise ValueError("账单参与人不完整。")
        share, extra = divmod(amount, len(members))
        balances[payer] += amount
        for index, name in enumerate(members):
            balances[name] -= share + int(index < extra)
    creditors = [[name,n] for name,n in balances.items() if n > 0]
    transfers = []
    for debtor, balance in balances.items():
        while balance < 0:
            creditor, available = creditors[0]
            value = min(available, -balance)
            transfers.append((debtor, creditor, value))
            balance += value
            creditors[0][1] -= value
            if not creditors[0][1]:
                creditors.pop(0)
    return transfers


def names(text, speaker):
    return list(dict.fromkeys(speaker if word.strip() == "我" else word.strip()
                              for word in re.split(r"[、,，/和与\s]+",text.strip("。 ")) if word.strip()))


def item(payer, amount, tail, people, speaker):
    payer = speaker if payer == "我" else payer.strip()
    if payer not in people:
        raise ValueError("付款人不在这桌名单里：" + payer)
    label, *allocation = re.split(r"[，,；;]\s*", tail.strip("，,：: "), maxsplit=1)
    label = label.strip() or "聚餐"
    members = list(people)
    if allocation:
        rule = allocation[0].strip("。 ")
        include = re.fullmatch(r"(?:只有|仅|只算|由)(.+?)(?:平摊|分摊|吃了|喝了|参加)?", rule)
        exclude = re.fullmatch(r"(?:除外|不含|不算|除了)(.+?)(?:以外|之外)?", rule) or re.fullmatch(r"(.+?)(?:没吃|没喝|不参与|不分摊)", rule)
        if include or exclude:
            chosen = names((include or exclude)[1],speaker)
            if not chosen or set(chosen) - set(people):
                raise ValueError("分摊名单要使用开账时的名字，可用顿号分隔。")
            members = chosen if include else [p for p in people if p not in chosen]
        elif rule not in {"大家平摊","全员平摊","全部平摊"}:
            raise ValueError("这笔由谁分？例如「只有小王、小林平摊」或「不含小林」。")
    if not members:
        raise ValueError("这笔至少要有一个分摊人。")
    return payer, cents(amount), label[:90], members


class DinnerSplit:
    title = "聚餐 AA"

    def interested(self,key,text,addressed):
        value = text.strip(" 。！!?？")
        return any(p.fullmatch(value) for p in (_OPEN,_ADD,_END,_UNDO,_PHOTO,_YES,_CLOSE))

    async def handle(self,host,event,key,text):
        room = json.dumps(key[:3] if key[2] else key)
        actor = key[-1]
        raw = event.message_obj.raw_message
        sender = raw.get("sender") or {}
        speaker = str(sender.get("card") or sender.get("nickname") or actor)
        store = PlanStore(host.runtime.project_root/"data/dafeiyu/dinner-split.sqlite3",_SCHEMA)
        text = text.strip(" 。！!?？")
        opened, added, undo = _OPEN.fullmatch(text), _ADD.fullmatch(text), _UNDO.fullmatch(text)
        photo, yes = _PHOTO.fullmatch(text), _YES.fullmatch(text)
        draft = None
        if photo:
            quoted,current = image_refs(event)
            if not (current or quoted):
                await event.send(event.plain_result("把小票图片一起发来，或者引用刚才的小票。"))
                return True
            observed = parse_object(await observe_image(host,(current or quoted)[:1],
                '只抄清晰的最终收费明细，各行用最终行金额；不要把小计、总计、实付重复记为商品。'
                '返回 JSON {"items":[{"name":"商品","amount":"1.20"}],"total":"实际合计"}。看不清的金额用 null。'))
            rows = observed.get("items")
            try:
                if not isinstance(rows,list) or not 1 <= len(rows) <= 100:
                    raise ValueError
                draft = [(x["name"][:70],cents(x["amount"])) for x in rows if isinstance(x["name"],str) and x["name"].strip()]
                if len(draft) != len(rows) or sum(x[1] for x in draft) != cents(observed.get("total")):
                    raise ValueError
            except (TypeError, KeyError, ValueError):
                await event.send(event.plain_result("小票的行金额和实付合计没能对上。可以发清晰些的照片，或按实付金额记一笔。"))
                return True
        def change(db):
            bill = db.execute("SELECT * FROM bills WHERE room=?",(room,)).fetchone()
            if opened:
                if bill:
                    return "本桌账本已开，名单是" + "、".join(json.loads(bill["people"])) + "。"
                people = names(opened[1] or "",speaker)
                if not 2 <= len(people) <= 60 or any(len(p)>24 for p in people):
                    return "告诉我有哪些人，比如「聚餐AA 小林、小王、我」。"
                db.execute("INSERT INTO bills VALUES (?,?,?)",(room,actor,json.dumps(people)))
                return "这桌记了" + "、".join(people) + "。接着说「记一笔 小王付60元晚饭」；个别菜可以补「，不含小林」。"
            if not bill:
                return None if added and not text.upper().startswith("AA") else "这边还没开账，可说「聚餐AA 小林、小王、我」。"
            people = json.loads(bill["people"])
            def add(payer, amount, label, members):
                return db.execute("INSERT INTO expenses(room,actor,payer,name,amount,people) VALUES (?,?,?,?,?,?)",
                                  (room,actor,payer,label,amount,json.dumps(members))).lastrowid
            if added:
                payer, amount, label, members = item(*added.groups(), people, speaker)
                seq = add(payer,amount,label,members)
                return f"第{seq}笔「{label}」{money(amount)}元，{payer}付，由" + "、".join(members) + "分。"
            if draft:
                if speaker not in people:
                    return "这张小票由谁付？请该付款人在名单里引用小票录入，或按项目手动记账。"
                db.execute("INSERT OR REPLACE INTO drafts VALUES (?,?,?)",(room,actor,json.dumps(draft)))
                return "我读到" + "、".join(f"{name} {money(value)}元" for name,value in draft) + f"。按{speaker}付款、全桌平摊；对得上请说「确认小票」，有部分人没吃可取消并分笔记。"
            if yes:
                saved = db.execute("SELECT rows FROM drafts WHERE room=? AND actor=?",(room,actor)).fetchone()
                db.execute("DELETE FROM drafts WHERE room=? AND actor=?",(room,actor))
                if not saved: return "你没有待确认的小票。"
                if text.startswith("取消"): return "这张小票已取消。"
                for label,value in json.loads(saved[0]):
                    add(speaker,value,label,people)
                return "小票明细已记入，可以说「AA结算」。"
            if undo:
                if undo[1]:
                    row = db.execute("SELECT id,actor FROM expenses WHERE room=? AND id=?",(room,int(undo[1]))).fetchone()
                else:
                    row = db.execute("SELECT id,actor FROM expenses WHERE room=? AND actor=? ORDER BY id DESC LIMIT 1",(room,actor)).fetchone()
                if not row or actor not in {bill["owner"],row["actor"]}: return "没有找到你可以撤销的那一笔。"
                db.execute("DELETE FROM expenses WHERE id=?", (row["id"],))
                return f"已撤销第{row['id']}笔。"
            if _CLOSE.fullmatch(text):
                if actor != bill["owner"]: return "由开账的人来结束这桌账本。"
                for table in ("bills","expenses","drafts"):
                    db.execute("DELETE FROM " + table + " WHERE room=?",(room,))
                return "这桌账本和小票草稿已清空。"
            rows = db.execute("SELECT * FROM expenses WHERE room=? ORDER BY id",(room,)).fetchall()
            if not rows: return "目前还没记花费。"
            details = [f"第{r['id']}笔 {r['name']} {money(r['amount'])}元（{r['payer']}垫）" for r in rows]
            transfers = settle(people,[(r["payer"],r["amount"],json.loads(r["people"])) for r in rows])
            return "合计 " + money(sum(r["amount"] for r in rows)) + "元\n" + "\n".join(details) + "\n" + (
                "\n".join(f"{a} 给 {b} {money(v)}元" for a,b,v in transfers) or "大家已经付平了。")
        try:
            reply = await asyncio.to_thread(store.turn,event,change,datetime.now(CHINA))
        except ValueError as exc:
            reply = str(exc)
        if reply is None: return False
        if reply: await event.send(event.plain_result(reply))
        return True
