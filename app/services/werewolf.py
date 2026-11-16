"""The QQ Werewolf judge. Only a seat's filtered view is exported to player strategy."""
from __future__ import annotations

from collections import Counter
from random import SystemRandom

RNG = SystemRandom()
WOLF, SEER, WITCH, HUNTER, VILLAGER = "狼人","预言家","女巫","猎人","村民"
RULES = ("6～12人，6～8人配2狼，9～12人配3狼。预言家、女巫、猎人各1名，其余是村民。"
         "夜里狼队各投一票，最高票并列则无人遇袭；预言家每晚查一位。女巫全局各有一瓶解药和毒药，同夜只能用一瓶，不能自救。"
         "白天讨论后，存活玩家公开投票，可弃票；平票只在并列者里重投一次，再平无人出局。"
         "猎人遇袭或被放逐时可以开枪，中毒不能开枪。猎人结算后，无狼则好人胜，狼人达到其余存活人数则狼队胜。")


def seat(user, nick, auto=False):
    return {"user":user, "nick":nick, "auto":auto, "alive":True, "role":""}


def lobby(owner, nick):
    return {"owner":owner,"phase":"lobby","day":0,"seats":[seat(owner,nick),seat("@fish","大肥鱼",True)],
            "log":[],"notes":{},"actions":{},"potions":{"save":True,"poison":True},"winner":""}


def announce(game, text):
    game["log"].append(text)
    game["serial"] = game.get("serial",0)+1
    game["log"] = game["log"][-90:]


def player(game, n):
    if type(n) is not int or not 1 <= n <= len(game["seats"]): raise ValueError("没有这个座位。")
    return game["seats"][n-1]


def living(game):
    return [n for n,p in enumerate(game["seats"],1) if p["alive"]]


def visible_board(game):
    return [{"号":n,"昵称":p["nick"],"存活":p["alive"]} for n,p in enumerate(game["seats"],1)]


def phase_name(game):
    return {"lobby":"报名中","wolves":"夜间","seer":"夜间","witch":"夜间","hunter":"等待猎人决定",
            "day":"白天讨论","vote":"白天投票","tie":"平票重投","over":"已结束"}[game["phase"]]


def start(game, *, practice=False, rng=RNG):
    if game["phase"] != "lobby": raise ValueError("这桌已经开局了。")
    if practice:
        while len(game["seats"]) < 8:
            n = len(game["seats"]) + 1
            game["seats"].append(seat(f"@npc{n}",f"练习玩家{n}",True))
    size = len(game["seats"])
    if not 6<=size<=12: raise ValueError("正式桌需要6到12位，想单人游玩可以说「狼人杀练习开局」。")
    wolf_count = 2 if size<=8 else 3
    roles = [WOLF]*wolf_count + [SEER,WITCH,HUNTER] + [VILLAGER]*(size-wolf_count-3)
    rng.shuffle(roles)
    for p,role in zip(game["seats"],roles): p["role"] = role
    announce(game,"已发身份。白天在群里讨论，夜间行动请私聊大肥鱼。")
    night(game)


def night(game):
    game.update(day=game["day"]+1,phase="wolves",actions={},victim=0,poisoned=0,healed=False,tied=[])
    announce(game,f"第{game['day']}夜，天黑请闭眼。")


def eligible(game):
    phase = game["phase"]
    if phase == "hunter": return [game["shooter"]]
    roles = {"wolves":WOLF,"seer":SEER,"witch":WITCH}
    if phase in roles: return [n for n in living(game) if player(game,n)["role"] == roles[phase]]
    return living(game) if phase in {"vote","tie"} else []


def choices(game, n):
    if n not in eligible(game): return []
    phase = game["phase"]
    others = [i for i in living(game) if i != n]
    if phase == "wolves": return [("袭击",i) for i in others if player(game,i)["role"] != WOLF] + [("跳过",0)]
    if phase == "seer": return [("查验",i) for i in others] + [("跳过",0)]
    if phase == "witch":
        legal = [("跳过",0)]
        if game["potions"]["save"] and game["victim"] and game["victim"] != n: legal.append(("救人",game["victim"]))
        if game["potions"]["poison"]: legal += [("用毒",i) for i in others]
        return legal
    if phase == "hunter": return [("开枪",i) for i in others] + [("跳过",0)]
    if phase in {"vote","tie"}:
        candidates = game["tied"] if phase=="tie" else living(game)
        return [("投票",i) for i in candidates] + [("弃票",0)]
    return []


def view_for(game, n):
    p = player(game,n)
    info = {"座位":n,"身份":p["role"],"第几天":game["day"],"公开阶段":phase_name(game),
            "玩家":visible_board(game),"公开发言":list(game["log"][-18:]),"上轮公开票型":dict(game.get("public_votes",{})),
            "可选行动":choices(game,n)}
    if p["role"] == WOLF:
        info["狼队座位"] = [i for i,q in enumerate(game["seats"],1) if q["role"] == WOLF]
        if game["phase"]=="wolves":
            info["狼队已选"] = {k:v[1] for k,v in game["actions"].items()}
    elif p["role"] == SEER:
        info["自己的查验"] = dict(game["notes"].get(str(n),{}))
    elif p["role"] == WITCH:
        info["自己的药"] = dict(game["potions"])
        if n in eligible(game) and game["phase"] == "witch" and game["potions"]["save"]: info["当晚遇袭"] = game["victim"]
    return info


def agent_move(view, rng=RNG):
    legal = view["可选行动"]
    if not legal: return None
    targets = [a for a in legal if a[1] and a[1] != view["座位"]]
    if not targets: return legal[-1]
    if "狼队已选" in view:
        for prior in view["狼队已选"].values():
            same = [a for a in targets if a == ("袭击",prior)]
            if same: return same[0]
    if view["身份"] == WITCH and any(a[0] == "救人" for a in targets):
        return next(a for a in targets if a[0]=="救人")
    if view["身份"] == WITCH and targets[0][0]=="用毒" and view["第几天"]<2: return ("跳过",0)
    checks = view.get("自己的查验",{})
    if targets[0][0] == "查验":
        targets = [a for a in targets if str(a[1]) not in checks] or targets
    if targets[0][0] in {"投票","开枪","用毒"}:
        known = [a for a in targets if checks.get(str(a[1])) == WOLF]
        if known: return rng.choice(known)
        targets = [a for a in targets if a[1] not in view.get("狼队座位",()) and checks.get(str(a[1])) != "好人"] or targets
        if prior := Counter(v for v in view["上轮公开票型"].values() if v):
            maximum = max(prior[a[1]] for a in targets)
            targets = [a for a in targets if prior[a[1]]==maximum]
    return rng.choice(targets)


def seat_speech(view):
    checks = view.get("自己的查验",{})
    if checks and view["身份"]==SEER:
        suspect = next((n for n,c in checks.items() if c==WOLF), None)
        if suspect: return f"我查过{suspect}号，是狼。先听听这个位置怎么解释。"
        return f"我手上的信息是{next(reversed(checks))}号属于好人阵营，今天先听别的位置。"
    votes = Counter(v for v in view["上轮公开票型"].values() if v)
    if votes:
        n = votes.most_common(1)[0][0]
        return f"上一轮{n}号收了不少票。我想听投过这个位置的人说说依据。"
    if view["身份"]==WOLF: return "先别急着定人。我想听第一个指认别人的玩家讲清楚依据。"
    if view["身份"]==WITCH: return "我会留心谁在用昨晚的结果强推结论。先让每个人说一轮。"
    if view["身份"]==HUNTER: return "先听大家报信息。投我的人也说说理由，我会记住你们的判断。"
    return "我手里没查验信息，先听谁能交代清楚自己的判断。"


def act(game, n, verb, target):
    if (verb,target) not in choices(game,n): raise ValueError("当前阶段不能这样行动，请查看自己的身份和可选动作。")
    if str(n) in game["actions"]: raise ValueError("你已经提交过这轮行动了。")
    game["actions"][str(n)] = [verb,target]
    if verb=="查验":
        answer = WOLF if player(game,target)["role"]==WOLF else "好人"
        game["notes"].setdefault(str(n),{})[str(target)] = answer
        return f"你的查验结果：{target}号属于{answer}阵营。"
    return f"已记下你的{verb}" + (f" · {target}号。" if target else "。")


def top(actions):
    tally = Counter(a[1] for a in actions.values() if a[1])
    return sorted(n for n,v in tally.items() if v == max(tally.values())) if tally else []


def after_deaths(game, source):
    live = living(game)
    wolves = sum(player(game,n)["role"] == WOLF for n in live)
    if not wolves or wolves >= len(live)-wolves:
        game.update(phase="over",winner="好人" if not wolves else "狼人",actions={})
        roles = "、".join(f"{i}号 {p['role']}" for i,p in enumerate(game["seats"],1))
        announce(game,f"{game['winner']}阵营获胜。本局身份 · {roles}")
    elif source=="night":
        game.update(phase="day",actions={})
        announce(game,"进入白天讨论。可以说「狼人杀发言 ……」，讨论后说「狼人杀开始投票」。")
    else:
        night(game)


def deaths(game, victims, source):
    victims = sorted({n for n in victims if n and player(game,n)["alive"]})
    for n in victims: player(game,n)["alive"] = False
    names = "、".join(f"{i}号" for i in victims)
    if source == "night": announce(game,"天亮了，"+(names+"出局。" if names else "昨夜无人出局。"))
    else: announce(game,names+"被放逐。" if names else "这轮无人被放逐。")
    hunter = next((i for i in victims if player(game,i)["role"]==HUNTER and (source!="night" or i!=game["poisoned"])),None)
    if hunter:
        game.update(phase="hunter",shooter=hunter,after_shot=source,actions={})
        announce(game,f"{hunter}号发动猎人技能，可以开枪或放弃。")
    else: after_deaths(game,source)


def resolve(game):
    phase, decisions = game["phase"], game["actions"]
    if phase=="wolves":
        choice = top(decisions)
        game.update(phase="seer",actions={},victim=choice[0] if len(choice)==1 else 0)
    elif phase=="seer": game.update(phase="witch",actions={})
    elif phase=="witch":
        for verb,target in decisions.values():
            if verb=="救人": game["potions"]["save"], game["healed"] = False, True
            if verb=="用毒": game["potions"]["poison"], game["poisoned"] = False, target
        deaths(game,[0 if game["healed"] else game["victim"],game["poisoned"]],"night")
    elif phase=="hunter":
        verb,target = next(iter(decisions.values()))
        if verb=="开枪":
            player(game,target)["alive"] = False
            announce(game,f"猎人带走了{target}号。")
        else: announce(game,"猎人放弃开枪。")
        after_deaths(game,game["after_shot"])
    elif phase in {"vote","tie"}:
        game["public_votes"] = {n:a[1] for n,a in decisions.items()}
        tally = "、".join(f"{n}号→{a[1]}号" if a[1] else f"{n}号弃票" for n,a in sorted(decisions.items(),key=lambda i:int(i[0])))
        announce(game,"票型 · "+tally)
        tied = top(decisions)
        if phase=="vote" and len(tied)>1:
            game.update(phase="tie",tied=tied,actions={})
            announce(game,"最高票并列，请在 "+"/".join(map(str,tied))+" 号中重投一次。")
        else: deaths(game,tied if len(tied)==1 else [],"vote")


def advance(game, rng=RNG):
    for _ in range(32):
        if game["phase"] in {"lobby","day","over"}: return
        needed = eligible(game)
        for n in needed:
            if player(game,n)["auto"] and str(n) not in game["actions"]:
                verb,target = agent_move(view_for(game,n),rng)
                act(game,n,verb,target)
        if any(str(n) not in game["actions"] for n in needed): return
        resolve(game)
    raise RuntimeError("game phase did not settle")


def begin_vote(game):
    if game["phase"] != "day": raise ValueError("现在还没有进入白天讨论。")
    game.update(phase="vote",actions={})
    announce(game,"开始投票。存活的玩家可说「投票3号」或「狼人杀弃票」。")
