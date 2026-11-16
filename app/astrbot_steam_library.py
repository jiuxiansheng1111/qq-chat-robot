"""Intersect only the libraries group members explicitly shared with this group."""
from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import datetime

import httpx

from app.services import game_clients
from app.services.bounded_http import request_json
from app.services.http_routing import outbound_http_client
from app.services.social_plans import CHINA, NUMBER, PlanStore, number

_SYNC = re.compile(r"^(?:共享|分享|同步|更新)(?:我的)?Steam(?:游戏)?库[ ：:]+(\d{17})$",re.IGNORECASE)
_MANUAL = re.compile(r"^(?:我(?:愿意)?共享的游戏|分享我的游戏清单|共享我的游戏|手动共享游戏)[：: ]+(.{1,600})$")
_GET = re.compile(r"共同游戏库|共同的Steam游戏|(?:我们|大家).{0,14}(?:都有|都能玩|一起能玩|能一起玩|能玩|一起玩)"
                  r".{0,10}(?:什么|哪些|哪款|哪几款)(?:Steam)?游戏|(?:我们|大家).{0,12}有(?:哪些|什么)共同游戏",re.IGNORECASE)
_SIZE = re.compile(rf"({NUMBER})\s*(?:个)?人")
_DROP = re.compile(r"^(?:撤回|取消|删除)(?:我的)?(?:Steam库共享|游戏库共享|共享游戏库|Steam共享|共享的游戏)$",re.IGNORECASE)
_SCHEMA = """CREATE TABLE IF NOT EXISTS libraries(room TEXT NOT NULL,person TEXT NOT NULL,nick TEXT NOT NULL,
    games TEXT NOT NULL, PRIMARY KEY(room,person));"""


async def api(url, **params):
    async with outbound_http_client(timeout=9) as client:
        return await request_json(client,"GET",url,params=params,max_bytes=4_000_000,timeout_seconds=10)


async def owned(steam_id):
    settings = game_clients.settings().get("steam") or {}
    key = settings.get("web_api_key")
    if not key: return None
    async with outbound_http_client(timeout=9) as client:
        envelope = await request_json(client,"GET","https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
                        headers={"x-webapi-key":key},params={"steamid":steam_id,"include_appinfo":1,"include_played_free_games":1},
                        max_bytes=16_000_000,timeout_seconds=12)
    response = envelope.get("response") or {}
    if not isinstance(response.get("game_count"),int): return None
    return {str(g["appid"]):{"title":g["name"]} for g in response.get("games",[]) if isinstance(g.get("appid"),int) and g.get("name")}


async def lookup(query):
    if re.fullmatch(r"\d{1,10}",query):
        appid = query
    else:
        result = await api("https://store.steampowered.com/api/storesearch/",term=query,l="schinese",cc="CN")
        matches = [v for v in result.get("items",[]) if v.get("type")=="app" and v.get("name","").casefold()==query.casefold()]
        if len(matches)!=1: return None
        appid = str(matches[0]["id"])
    data = await details(appid)
    return (appid,{"title":data["name"]}) if data.get("type") == "game" and data.get("name") else None


async def details(appid):
    data = await api("https://store.steampowered.com/api/appdetails",appids=appid,l="schinese",cc="CN")
    entry = (data or {}).get(str(appid)) or {}
    return (entry.get("data") or {}) if entry.get("success") is True else {}


async def store_details(appid):
    try:
        return await details(appid)
    except (TimeoutError, httpx.HTTPError, ValueError):
        return {}


def capacity(data):
    ids = {c.get("id") for c in data.get("categories",[]) if isinstance(c,dict)}
    if not ids.intersection({1,9,36,38}):
        return None, None, "单人或缺少多人标记"
    text = html.unescape(re.sub(r"<[^>]+>"," ",str(data.get("short_description",""))+" "+str(data.get("about_the_game",""))))
    limit = re.search(r"(?:最多|支持至多|最多可供|up to)\s*(\d{1,3})\s*(?:位|名|个|人|players)",text,re.IGNORECASE)
    span = re.search(r"(\d{1,3})\s*[-–~至到]\s*(\d{1,3})\s*(?:位|名|个|人|players)",text,re.IGNORECASE)
    if span and 0<int(span[1])<=int(span[2]): return int(span[1]),int(span[2]),"商店标注"
    if limit and int(limit[1]) >= 2: return 2,int(limit[1]),"商店标注上限"
    return 2,None,"商店标记多人，人数上限待确认"


async def compare(exact, library):
    sets = [json.loads(library[p]["games"]) for p in exact]
    common = sorted(set(sets[0]).intersection(*sets[1:]),key=lambda i:sets[0][i]["title"].casefold())
    count = len(exact)
    lines = ["本次比较：" + "、".join(library[p]["nick"] for p in exact) + f"（{count}人）。"]
    verified, candidates = [], []
    checked = unavailable = 0
    for offset in range(0, min(len(common),30),6):
        batch = common[offset:offset+6]
        for appid, data in zip(batch, await asyncio.gather(*(store_details(i) for i in batch))):
            checked += 1
            if not data:
                unavailable += 1
                continue
            low, high, note = capacity(data)
            if low is None or count < low or high is not None and count > high:
                continue
            cats = {v.get("id") for v in data.get("categories",[]) if isinstance(v, dict)}
            if cats.intersection({36,38}):
                mode = "，在线联机"
            elif cats.intersection({24,37,39}):
                mode = "，商店列有同屏玩法"
            else:
                mode = ""
            limits = f"{low}～{high}人" if high and note == "商店标注" else f"最多{high}人" if high else note
            line = sets[0][appid]["title"] + "（" + limits + mode + "）"
            (verified if high else candidates).append(line)
        if len(verified) >= 6:
            break
    if verified: lines.append("人数符合：" + "、".join(verified[:6]))
    if candidates: lines.append("需核实人数：" + "、".join(candidates[:6]))
    if not verified and not candidates:
        lines.append("共同库里暂未核实到适合这桌人数的多人游戏。" if common else "共享清单没有交集。")
    if unavailable: lines.append(f"有{unavailable}款暂时读不到商店资料。")
    if checked < len(common): lines.append(f"本次核对了共有的{len(common)}款中的前{checked}款。")
    return "\n".join(lines)


class SteamLibrary:
    title = "Steam 共同游戏库"

    def interested(self,key,text,addressed):
        text = text.strip(" 。！？?!")
        return bool(key[2] and (_SYNC.fullmatch(text) or _MANUAL.fullmatch(text) or _GET.search(text) or _DROP.fullmatch(text)))

    async def handle(self,host,event,key,text):
        text = text.strip(" 。！？?!")
        room,person = json.dumps(key[:3]),key[3]
        raw = event.message_obj.raw_message
        nick = str((raw.get("sender") or {}).get("card") or (raw.get("sender") or {}).get("nickname") or person)
        store = PlanStore(host.runtime.project_root/"data/dafeiyu/steam-libraries.sqlite3",_SCHEMA)
        sync,manual = _SYNC.fullmatch(text),_MANUAL.fullmatch(text)
        shares = None
        if sync:
            shares = await owned(sync[1])
            if shares is None:
                await event.send(event.plain_result("没读到可共享的游戏列表。你也可以说「共享我的游戏：游戏名、游戏名」，同名游戏可以填 Steam AppID。"))
                return True
        if manual:
            terms = list(dict.fromkeys(w.strip() for w in re.split(r"[、，,;；]",manual[1]) if w.strip()))
            if not 1 <= len(terms) <= 25:
                await event.send(event.plain_result("每次可分享 1 到 25 款游戏，游戏之间用顿号分隔。"))
                return True
            shares = {}
            for offset in range(0,len(terms),5):
                batch = terms[offset:offset+5]
                for query, found in zip(batch, await asyncio.gather(*(lookup(term) for term in batch))):
                    if not found:
                        await event.send(event.plain_result("「"+query+"」没有唯一匹配的 Steam 游戏，给我它的 AppID 再存这份清单吧。"))
                        return True
                    shares[found[0]] = found[1]
        def change(db):
            if shares is not None:
                db.execute("INSERT OR REPLACE INTO libraries VALUES (?,?,?,?)",(room,person,nick,json.dumps(shares,ensure_ascii=False)))
                return f"已在本群共享你选的{len(shares)}款游戏，可以随时说「撤回我的Steam库共享」。"
            if _DROP.fullmatch(text):
                db.execute("DELETE FROM libraries WHERE room=? AND person=?",(room,person))
                return "你在这个群共享的清单已撤回。"
            return db.execute("SELECT person,nick,games FROM libraries WHERE room=? ORDER BY person",(room,)).fetchall()
        result = await asyncio.to_thread(store.turn,event,change,datetime.now(CHINA))
        if isinstance(result,list):
            library = {r["person"]:r for r in result}
            mentioned = list(dict.fromkeys(str(seg["data"]["qq"]) for seg in raw.get("message",[])
                if isinstance(seg,dict) and seg.get("type")=="at" and str((seg.get("data") or {}).get("qq","")).isdigit()
                and str(seg["data"]["qq"])!=key[1]))
            exact = list(dict.fromkeys([person,*mentioned])) if mentioned else list(library)
            wanted = _SIZE.search(text)
            if person not in library or len(exact)<2 or set(exact)-library.keys() or wanted and number(wanted[1])!=len(exact):
                reply = "请让这次要一起玩的每个人在本群自愿共享游戏。只比较部分群友时，在问题里 @大肥鱼 和他们。"
            else:
                reply = await compare(exact, library)
                current = await asyncio.to_thread(store.transaction, change)
                current = {row["person"]:row["games"] for row in current}
                if any(current.get(p) != library[p]["games"] for p in exact):
                    reply = "有人更新了共享清单，请再问我一次。"
        else: reply = result
        if reply: await event.send(event.plain_result(reply))
        return True
