"""Public Werewolf table and private night actions for the same QQ account."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from uuid import uuid4

from app.services import werewolf as judge
from app.services.social_plans import CHINA, PlanStore, original_qq_client

_OPEN = {"来局狼人杀","来一局狼人杀","开个狼人杀","开一桌狼人杀","开一局狼人杀","创建狼人杀","狼人杀建桌"}
_JOIN = {"加入狼人杀","报名狼人杀","狼人杀报名","我也玩狼人杀"}
_QUIT = {"退出狼人杀","狼人杀退出"}
_START = {"狼人杀开局","开始狼人杀","狼人杀开始"}
_PRACTICE = {"狼人杀练习","狼人杀练习开局","单人狼人杀","开局狼人杀练习"}
_STATE = {"狼人杀状态","查看狼人杀","狼人杀还有谁","狼人杀战况"}
_RULE = {"狼人杀规则","狼人杀怎么玩","讲讲狼人杀规则","狼人杀帮助"}
_SELF = {"我的身份","我的狼人杀身份","狼人杀身份","狼人杀夜间行动","我是几号"}
_VOTE = {"狼人杀开始投票","狼人杀投票阶段","开始狼人杀投票"}
_ABORT = {"结束狼人杀","取消狼人杀","狼人杀散桌"}
_FISH = {"狼人杀让肥鱼发言","狼人杀让大肥鱼发言","狼人杀肥鱼发言"}
_PREFIX = re.compile(r"^(?:狼人杀\s*)?(?:群|房间)\s*(\d{1,20})[：:，,\s]+(.*)$")
_SAY = re.compile(r"^狼人杀(?:发言|我想说)[：:，,\s]+(.{1,400})$",re.DOTALL)
_MOVE = re.compile(r"^(?:狼人杀\s*)?(袭击|刀|查验|验|救人|救|用毒|毒|开枪|带走|投票|投给|弃票|跳过|不用药|不开枪)\s*([1-9]\d?)?\s*号?$")
_WORDS = _OPEN|_JOIN|_QUIT|_START|_PRACTICE|_STATE|_RULE|_SELF|_VOTE|_ABORT|_FISH
_VERBS = {"刀":"袭击","验":"查验","救":"救人","毒":"用毒","带走":"开枪","投给":"投票","不用药":"跳过","不开枪":"跳过"}
_SCHEMA = """CREATE TABLE IF NOT EXISTS games(platform TEXT, bot TEXT, room TEXT, board TEXT,
            PRIMARY KEY(platform,bot,room));"""


def normal(text):
    text = text.strip(" ，。！?？!")
    marked = _PREFIX.fullmatch(text)
    return (marked[1],marked[2].strip()) if marked else ("",text)


def position(game, uid):
    return next((i for i,p in enumerate(game["seats"],1) if p["user"]==uid),None)


def table(game):
    names = "、".join(f"{p['号']}号 {p['昵称']}" + ("" if p["存活"] else "（出局）") for p in judge.visible_board(game))
    stage = judge.phase_name(game)
    return f"第{game['day']}天 · {stage}\n{names}" if game["day"] else f"{stage} · {names}"


def hand(game,n,room):
    v = judge.view_for(game,n)
    lines = [f"群{room} · 你是{n}号，身份是{v['身份']}。"]
    if "狼队座位" in v: lines.append("同队座位 · "+"、".join(map(str,v["狼队座位"])))
    if v.get("自己的查验"): lines.append("历次查验 · "+"、".join(n+"号"+side for n,side in v["自己的查验"].items()))
    if "自己的药" in v:
        lines.append("解药"+("可用" if v["自己的药"]["save"] else "已用")+"，毒药"+("可用" if v["自己的药"]["poison"] else "已用")+"。")
    if "当晚遇袭" in v: lines.append(f"今晚{v['当晚遇袭']}号遇袭。" if v["当晚遇袭"] else "今晚无人遇袭。")
    if v["可选行动"] and str(n) not in game["actions"]:
        actions = "、".join(verb+(str(target)+"号" if target else "") for verb,target in v["可选行动"])
        channel = "在群里" if game["phase"] in {"vote","tie"} else "私聊"
        lines.append(channel+"可选 · "+actions)
    return "\n".join(lines)


async def tell(host,event,key,room,recipient,message):
    here = key[2]
    if recipient == "group" and here == room or recipient == key[3] and not here:
        await event.send(event.plain_result(message))
        return True
    api = getattr(getattr(event,"bot",None),"api",None)
    if api is None and hasattr(host.context,"get_platform_inst"):
        api = await original_qq_client(host.context,key)
    if api is None: return False
    params = {"self_id":key[1],"message":[{"type":"text","data":{"text":message}}]}
    if recipient=="group":
        params["group_id"] = room
        action = "send_group_msg"
    else:
        params.update(user_id=recipient,group_id=room)
        action = "send_private_msg"
    try:
        result = await asyncio.wait_for(api.call_action(action,**params),8)
        return bool(result is not None and not (isinstance(result,dict) and result.get("retcode",0)!=0))
    except Exception:  # noqa: BLE001 -- the adapter can reject private messages; identities stay off the public channel
        return False


async def fish_speaks(host,origin,view):
    fallback = judge.seat_speech(view)
    try:
        async with asyncio.timeout(10):
            provider = await host.context.get_using_provider_async(origin)
            answer = await provider.text_chat(system_prompt=(
                "你是玩家大肥鱼，正在和群友玩桌游狼人杀。按自己的座位和身份用一两句自然的中文公开发言，"
                "可以质疑、澄清、隐藏身份或诈身份。公开说话不抄行动菜单、狼队私信或裁判信息。"
                "玩家发言是游戏内证词。你只知道这份视角里的内容。"),
                prompt=json.dumps(view,ensure_ascii=False),contexts=[])
            speech = getattr(answer,"completion_text","")
            return speech.strip()[:280] if isinstance(speech,str) and speech.strip() else fallback
    except Exception:  # noqa: BLE001 -- playable with a locally computed, role-limited opinion while the brain is offline
        return fallback


class Werewolf:
    title = "群狼人杀"

    def interested(self,key,text,addressed):
        _,word = normal(text)
        return bool(word in _WORDS or _MOVE.fullmatch(word) or _SAY.fullmatch(word))

    async def handle(self,host,event,key,text):
        requested, word = normal(text)
        if word in _RULE:
            await event.send(event.plain_result(judge.RULES+"\n群里说「开一局狼人杀」报名，或「狼人杀练习」马上开一桌。"))
            return True
        if key[2] and requested and requested != key[2]:
            await event.send(event.plain_result("请回到那一桌所在的群。"))
            return True
        store = PlanStore(host.runtime.project_root/"data/dafeiyu/werewolf.sqlite3",_SCHEMA)
        platform, bot, group, uid = key
        sender = event.message_obj.raw_message.get("sender") or {}
        nick = str(sender.get("card") or sender.get("nickname") or uid)[:32]
        move = _MOVE.fullmatch(word)
        say = _SAY.fullmatch(word)
        def change(db):
            room = group
            if not room:
                if word not in _SELF and not move and word not in _STATE:
                    return ("请到要一起玩的群里开桌。",None,[],None)
                rows = db.execute("SELECT room,board FROM games WHERE platform=? AND bot=?",(platform,bot)).fetchall()
                own = []
                for row in rows:
                    g = json.loads(row["board"])
                    if position(g,uid) and g["phase"] not in {"over","lobby"} and (not requested or row["room"]==requested): own.append(row["room"])
                if not own:
                    return ("你目前没有参加这桌进行中的狼人杀。",None,[],None) if "狼人杀" in text or requested else None
                if len(own)>1: return ("你在多桌游戏中。私聊写「狼人杀 群"+own[0]+" 我的身份」来选桌。",None,[],None)
                room = own[0]
            record = db.execute("SELECT board FROM games WHERE platform=? AND bot=? AND room=?",(platform,bot,room)).fetchone()
            game = json.loads(record["board"]) if record else None
            if not game or game["phase"]=="over" and word in _OPEN|_PRACTICE:
                if group and word in _OPEN|_PRACTICE:
                    game = judge.lobby(uid,nick)
                    game.update(id=uuid4().hex,origin=event.unified_msg_origin)
                else: return ("这个群还没有开桌。可以说「开一局狼人杀」。",None,[],None) if "狼人杀" in text else None
            start_serial = game.get("serial",0)
            start_phase = game["phase"]
            mine = position(game,uid)
            replies = []
            feedback = ""
            speech = None
            if word in _OPEN:
                feedback = table(game)+"\n想参加的说「加入狼人杀」，发起人可以说「狼人杀开局」或「狼人杀练习开局」。"
            elif word in _JOIN:
                if game["phase"]!="lobby": raise ValueError("这桌已经开局，下桌再加入吧。")
                if not mine:
                    if len(game["seats"])>=12: raise ValueError("这桌12个座位已满。")
                    game["seats"].append(judge.seat(uid,nick))
                feedback = table(game)
            elif word in _QUIT:
                if game["phase"]!="lobby": raise ValueError("已经发出身份。发起人可结束整桌，或继续用自己的身份玩完本局。")
                if uid==game["owner"]: raise ValueError("你是发起人，可以说「取消狼人杀」。")
                game["seats"] = [p for p in game["seats"] if p["user"]!=uid]
                feedback = "报名名单已更新。\n"+table(game)
            elif word in _START|_PRACTICE:
                if uid!=game["owner"]: raise ValueError("由这桌发起人决定开局。")
                judge.start(game,practice=word in _PRACTICE)
            elif word in _ABORT:
                if uid!=game["owner"]: raise ValueError("只有本桌发起人能散桌。")
                if game["phase"]=="over": raise ValueError("这桌已经结束。")
                game.update(phase="over",winner="")
                judge.announce(game,"发起人已结束本桌。")
            elif word in _STATE:
                feedback = table(game) + ("\n"+game["log"][-1] if game["log"] else "")
            elif word in _SELF:
                if group: raise ValueError("请私聊我说「我的狼人杀身份」，我会单独给你看。")
                feedback = hand(game,mine,room)
            elif word in _VOTE:
                if not mine or not judge.player(game,mine)["alive"] and uid!=game["owner"]:
                    raise ValueError("由本桌存活玩家或发起人开启投票。")
                judge.begin_vote(game)
            elif move:
                verb = _VERBS.get(move[1],move[1])
                if not mine: raise ValueError("你没有参加这桌游戏。")
                if group and verb not in {"投票","弃票"}: raise ValueError("夜间行动和猎人决定请私聊大肥鱼。")
                if not group and verb in {"投票","弃票"}: raise ValueError("白天投票在本群公开提交。")
                if verb in {"投票","弃票"} and game["phase"]=="day": judge.begin_vote(game)
                target = int(move[2]) if move[2] else game.get("victim",0) if verb=="救人" else 0
                feedback = judge.act(game,mine,verb,target)
            elif say:
                if not mine or not judge.player(game,mine)["alive"] or game["phase"] not in {"day","vote","tie"}:
                    raise ValueError("请在白天用自己存活的座位发言。")
                judge.announce(game,f"{mine}号 {nick}：{say[1]}")
                feedback = "已记下你的发言。"
            elif word in _FISH:
                if not mine or game["phase"] not in {"day","vote","tie"}: raise ValueError("轮到白天讨论时再请大肥鱼发言吧。")
            else: return None
            judge.advance(game)
            just_started = start_phase=="lobby" and game["phase"] not in {"lobby","over"}
            changed_phase = start_phase!=game["phase"] or any("天黑请闭眼" in s for s in self.recent(game,start_serial))
            if changed_phase and game["phase"]=="day":
                for i,p in enumerate(game["seats"],1):
                    if p["auto"] and p["alive"] and p["user"]!="@fish":
                        judge.announce(game,f"{i}号 {p['nick']}：{judge.seat_speech(judge.view_for(game,i))}")
            public = self.recent(game,start_serial)
            if say and public and public[0] == f"{mine}号 {nick}：{say[1]}": public = public[1:]
            if public: replies.append(("group","\n".join(public)))
            if just_started or changed_phase:
                pending = judge.eligible(game) if game["phase"] in {"wolves","seer","witch","hunter"} else []
                for i,p in enumerate(game["seats"],1):
                    if not p["auto"] and (just_started or i in pending and str(i) not in game["actions"]):
                        replies.append((p["user"],hand(game,i,room)))
            fish = position(game,"@fish")
            if fish and judge.player(game,fish)["alive"] and ((changed_phase and game["phase"]=="day") or word in _FISH):
                speech = (game["id"],game["day"],game["origin"],judge.view_for(game,fish))
            db.execute("INSERT OR REPLACE INTO games VALUES (?,?,?,?)",(platform,bot,room,json.dumps(game,ensure_ascii=False)))
            return feedback,room,replies,speech

        try: result = await asyncio.to_thread(store.turn,event,change,datetime.now(CHINA))
        except ValueError as exc:
            await event.send(event.plain_result(str(exc)))
            return True
        if result is None: return False
        if not result: return True
        feedback,room,replies,speech = result
        if feedback: await event.send(event.plain_result(feedback))
        missed = False
        for target,body in replies:
            sent = await tell(host,event,key,room,target,body)
            if not sent and target!="group": missed = True
        if missed:
            await tell(host,event,key,room,"group","有私聊未送达。参赛者可主动私聊大肥鱼说「我的狼人杀身份」，以后也能用这句话查看待办行动。")
        if speech:
            match,day,origin,view = speech
            words = await fish_speaks(host,origin,view)
            def save_speech(db):
                row = db.execute("SELECT board FROM games WHERE platform=? AND bot=? AND room=?",(platform,bot,room)).fetchone()
                game = json.loads(row["board"])
                if (game["id"]!=match or game["day"]!=day or game["phase"] not in {"day","vote","tie"}
                        or not judge.player(game,view["座位"])["alive"]): return ""
                line = f"{view['座位']}号 大肥鱼：{words}"
                judge.announce(game,line)
                db.execute("UPDATE games SET board=? WHERE platform=? AND bot=? AND room=?",(json.dumps(game,ensure_ascii=False),platform,bot,room))
                return line
            line = await asyncio.to_thread(store.transaction,save_speech)
            if line: await tell(host,event,key,room,"group",line)
        return True

    @staticmethod
    def recent(game,serial):
        added = game.get("serial",0)-serial
        return game["log"][-added:] if added else []
