"""Explicit per-viewer progress and discussion grounded only in episodes already watched."""
from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime

import httpx

from app.astrbot_compare import image_refs, parse_object
from app.astrbot_turn_features import observe_image
from app.services.bounded_http import request_json
from app.services.social_plans import CHINA, PlanStore, number

_EP = re.compile(r"第?\s*(\d{1,4}|[零〇一二两三四五六七八九十百千]+)\s*[集话話]")
_BOOK = re.compile(r"《([^《》\n]{1,80})》\s*(第[一二三四五六七八九十\d]+季)?")
_SAVE = re.compile(r"(?:帮我|替我)?(?:记(?:一下|下来|住|到)|记录(?:一下)?|存(?:一下|到))|更新(?:一下)?(?:我的)?(?:追番|看番|看剧)?进度|^追番进度")
_WATCH = re.compile(r"追到|看(?:完|到)|追完")
_SHOW = re.compile(r"(?:我(?:最近|现在)?(?:在)?(?:看|追)(?!到|完)(?:的番是)?|(?:番名|剧名|片名)是)[《 ]?(.{1,60}?)(?=》|[，,。]|(?:我)?(?:已(?:经)?)?(?:看|追)(?:到|完)|第.{1,5}[集话]|$)")
_NAMED_PROGRESS = re.compile(r"^(?:(?:帮我|替我)?(?:记一下|记住|记录一下|更新进度)[，,：: ]*)?"
                             r"([\w·・：: -]{1,60}?)[，, ]*(?:我)?(?:已经|刚刚|刚|只)?(?:看到|追到|看完|追完)第?\s*[\d零〇一二两三四五六七八九十百千]+[集话話]")
_STATUS = re.compile(r"(?:我的)?(?:追番|看番|看剧)进度(?:呢|是什么)?$|(?:上次|我).{0,40}(?:看到|追到)(?:哪|第几)[集话]")
_FORGET = re.compile(r"(?:忘掉|删除|清掉|清除|别记).{0,90}(?:进度|追番)|(?:取消追踪|不追了)")
_FUTURE = re.compile(r"结局(?:.{0,6}(?:怎样|怎么样|是什么|是啥|如何|谁|死|活)|[呢吗]?$)|"
                     r"(?:后面|后来|最后).{0,8}(?:死了|会死|死吗|死掉|活下来|活着|是谁|和谁|在一起|黑化|会怎么样)|(?:真凶|幕后黑手|凶手)(?:是谁|到底是谁)")
_NO_SPOILERS = re.compile(r"别剧透|无剧透|不要剧透|不剧透")
_TALK = re.compile(r"之前.{0,12}(?:出现|提到|见过|登场)|(?:出现|登场)过吗|前情|不(?:要)?剧透|别剧透|无剧透|这(?:个)?(?:人|角色)是谁|"
                   r"前.{1,5}集.{0,10}(?:发生|讲了|回顾)")
_YES = re.compile(r"好(?:的|啊|呀)?|可以|行|记吧|记住|对")
_NO = re.compile(r"不用了?|算了|先别记|不记了")
_SCHEMA = """CREATE TABLE IF NOT EXISTS shows
    (owner TEXT NOT NULL, title TEXT NOT NULL, episode INTEGER NOT NULL, updated INTEGER NOT NULL, PRIMARY KEY(owner,title));"""
_AGENT = "QQChatRobot/0.5 (https://github.com/jiuxiansheng1111/qq-chat-robot)"


def episode(value):
    if not value:
        return 0
    if value.isdecimal():
        return int(value)
    result = digit = 0
    for c in value:
        if c in "十百千":
            result += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[c]
            digit = 0
        else:
            digit = number(c)
    return result + digit


def title_in(text):
    match = _BOOK.search(text)
    if match:
        return (match[1].strip() + (" " + match[2] if match[2] else "")).strip()
    match = _SHOW.search(text)
    if match:
        return match[1].strip(" ，,")
    match = _NAMED_PROGRESS.search(text)
    if match and match[1].strip() not in {"我", "我才", "刚", "刚刚", "最近", "现在", "已经", "我已经", "帮我记一下", "记一下"}:
        return match[1].strip()
    return ""


def _key(title):
    return re.sub(r"[\s·・！!：:]", "", unicodedata.normalize("NFKC", title)).casefold()


async def episode_evidence(title, limit):
    """Bangumi subject descriptions and out-of-range episode names never enter the model."""
    async with httpx.AsyncClient(trust_env=False) as client:
        async def read(method, path, **kwargs):
            return await request_json(client, method, "https://api.bgm.tv/v0/" + path, max_bytes=2_000_000,
                                      timeout_seconds=8, headers={"User-Agent": _AGENT}, **kwargs)
        hits = await read("POST", "search/subjects", params={"limit": 12},
                          json={"keyword": title, "filter": {"type": [2], "nsfw": False}, "sort": "match"})
        matches = {s["id"] for s in hits.get("data", []) if s.get("type") == 2
                   and _key(title) in {_key(s.get("name_cn") or ""), _key(s.get("name") or "")}}
        if len(matches) != 1:
            return {}
        subject = matches.pop()
        evidence = {}
        for offset in range(0, limit, 100):
            size = min(100, limit - offset)
            page = await read("GET", "episodes", params={"subject_id": subject, "type": 0, "offset": offset, "limit": size})
            rows = page.get("data", [])
            for row in rows:
                num = row.get("ep") or row.get("sort")
                if (row.get("subject_id") != subject or row.get("type") != 0 or not isinstance(num, (int, float))
                        or not float(num).is_integer() or not 1 <= num <= limit):
                    continue
                # Episode titles alone cannot establish a character's appearance.
                desc = re.sub(r"<[^>]+>", "", str(row.get("desc") or "")).strip()
                if desc:
                    evidence[int(num)] = desc[:1600]
            if len(rows) < size or offset + size >= page.get("total", 0):
                break
        return evidence


class Watchlist:
    title = "无剧透追番"

    def __init__(self, clock=time.monotonic):
        self.pending = OrderedDict()
        self.clock = clock
        self.stores = {}

    def interested(self, key, text, addressed):
        pending = self.pending.get(key)
        if pending and self.clock() >= pending[0]:
            del self.pending[key]
            pending = None
        return bool(pending or _STATUS.search(text.rstrip("。？?！!")) or _FORGET.search(text) or _TALK.search(text) or _FUTURE.search(text)
                    or _EP.search(text) and (_SAVE.search(text) or addressed and _WATCH.search(text))
                    or _BOOK.search(text) and re.search(r"这集|剧情|角色|之前|后面|结局|讲了什么|前情|第.{1,4}集", text))

    def propose(self, key, title, num, consent):
        self.pending[key] = (self.clock() + 300, title, num, consent)
        self.pending.move_to_end(key)
        if len(self.pending) > 128:
            self.pending.popitem(last=False)

    async def handle(self, host, event, key, text):
        root = host.runtime.project_root
        if root not in self.stores:
            self.stores[root] = PlanStore(root / "data" / "watch-progress.sqlite3", _SCHEMA)
        store = self.stores[root]
        owner = json.dumps(key)
        text = text.strip(" ，。！？!?～")
        if text.startswith(("他说", "她说", "比如", "例如", "如果", "假如", "不是我", "我朋友", "我同学", "我没有", "我没", "我还没", "我不想")) or re.search(r"(?:还没|还未|没有)(?:追到|看到|看完|追完)", text):
            return False
        match = _EP.search(text)
        num = episode(match[1]) if match else 0
        named = title_in(text)
        pending = self.pending.get(key)
        save = bool(match and _SAVE.search(text) and not _FORGET.search(text))
        report = bool(match and _WATCH.search(text) and not _TALK.search(text))
        status, forget = _STATUS.search(text), _FORGET.search(text)
        # A viewer may give a cutoff just for this question, without consenting to storage.
        if named and 1 <= num <= 5000 and not (save or forget or status) and _WATCH.search(text) and (_TALK.search(text) or _FUTURE.search(text)):
            if _FUTURE.search(text):
                reply = f"你看到《{named}》第 {num} 集，后面的情节我先留给你自己看"
            else:
                reply = await self.discuss(host, event, named, num, text)
            await event.send(event.plain_result(reply))
            return True
        if pending:
            _, old_title, old_ep, consent = pending
            if _NO.fullmatch(text):
                self.pending.pop(key)
                await event.send(event.plain_result("好，这次就不记了"))
                return True
            if old_title and _YES.fullmatch(text):
                named, num, save = old_title, old_ep, True
            elif not old_title and (named or re.fullmatch(r"[\w·：: -]{1,45}", text)) and not (save or report or status or forget or _TALK.search(text)):
                named, num, save, report = named or text, old_ep, consent, True
            elif not (save or report or status or forget or _TALK.search(text)):
                self.pending.pop(key, None)
                return False
        if not (save or report or named or status or forget or _TALK.search(text) or _FUTURE.search(text)):
            return False
        if not (save or report or status or forget) and not store.path.exists():
            if _NO_SPOILERS.search(text) or named and (_TALK.search(text) or _FUTURE.search(text)):
                await event.send(event.plain_result("你在看哪部、看到第几集了？我按这个进度聊"))
                return True
            return False
        def read(db):
            return [tuple(r) for r in db.execute("SELECT title,episode FROM shows WHERE owner=? ORDER BY updated DESC", (owner,))]
        shows = await asyncio.to_thread(store.transaction, read)
        known = dict(shows)
        target = named or (shows[0][0] if shows else "")
        if forget:
            if not target or target not in known:
                reply = "这间会话没有这部作品的追番进度"
            elif not named and len(shows) > 1:
                reply = "想删哪一部？可以说「忘掉《片名》的进度」"
            else:
                await asyncio.to_thread(store.transaction, lambda db: db.execute("DELETE FROM shows WHERE owner=? AND title=?", (owner, target)).rowcount)
                reply = f"《{target}》的进度删掉了"
            self.pending.pop(key, None)
        elif status:
            rows = [(named, known[named])] if named and named in known else [] if named else shows
            reply = "、".join(f"《{t}》第 {n} 集" for t, n in rows) or "我这里还没记过这个进度"
        elif save or report:
            if not 1 <= num <= 5000:
                reply = "你看到第几集了？可以用数字告诉我"
            elif not named and len(shows) != 1:
                self.propose(key, "", num, save)
                reply = f"第 {num} 集是哪部作品？发一下片名就好"
            elif not save:
                self.propose(key, target, num, False)
                reply = f"要帮你记住《{target}》看到第 {num} 集吗？"
            else:
                def write(db):
                    db.execute("INSERT INTO shows VALUES (?,?,?,?) ON CONFLICT(owner,title) DO UPDATE SET episode=excluded.episode,updated=excluded.updated",
                               (owner, target, num, time.time_ns()))
                    return True
                saved = await asyncio.to_thread(store.turn, event, write, datetime.now(CHINA))
                if saved == "":
                    return True
                self.pending.pop(key, None)
                reply = f"记住了，《{target}》看到第 {num} 集。聊这部时我就以这集为界"
        elif not target or target not in known:
            if not (_NO_SPOILERS.search(text) or named and (_TALK.search(text) or _FUTURE.search(text))):
                return False
            reply = f"《{named}》你看到第几集了？" if named else "你在看哪部、看到第几集了？我按这个进度聊"
        elif not named and len(shows) > 1:
            reply = "你指的是哪部？把片名带上再问我，我按那一部的进度聊"
        elif (match and num > known[target]) or _FUTURE.search(text):
            reply = f"你记的是《{target}》第 {known[target]} 集。后面的情节我先留给你自己看"
        else:
            reply = await self.discuss(host, event, target, min(num, known[target]) if num else known[target], text)
        await event.send(event.plain_result(reply))
        return True

    async def discuss(self, host, event, title, limit, question):
        quoted, current = image_refs(event)
        observation = ""
        if quoted or current:
            try:
                observation = await observe_image(host, (quoted + current)[:3], "仅写当前截图可见的外观、场景和可读字幕。不要推断身份、结局或图外剧情。")
            except Exception:  # noqa: BLE001 -- an unread screenshot is not evidence of identity.
                return "这张截图我没看清。能告诉我是哪一集、画面里的人长什么样吗？"
        try:
            async with asyncio.timeout(20):
                facts = await episode_evidence(title, limit)
        except (ValueError, KeyError, TypeError, TimeoutError, httpx.HTTPError):
            facts = {}
        facts = {n: desc for n, desc in facts.items() if 1 <= n <= limit
                 and all(episode(m[1]) <= limit for m in _EP.finditer(desc))}
        fallback = f"手头前 {limit} 集的分集资料没法确认这一点。你指的是哪一集、哪句台词？"
        if not facts:
            return fallback
        if "前情" in question and not observation:
            return "\n".join(f"第 {n} 集：{v[:75]}" for n, v in sorted(facts.items())[-3:])
        # Ask for a short extract, then validate it against the bounded source before sending.
        selected = sorted(facts, key=lambda n: (sum(q in facts[n] for q in re.findall(r"[\u4e00-\u9fff]{2,4}|\w{3,}", question)), n), reverse=True)[:40]
        data = {"问题": question, "截图中可见": observation, "已看分集资料": {n: facts[n][:900] for n in selected}}
        provider = await host.context.get_using_provider_async(event.unified_msg_origin)
        async with asyncio.timeout(35):
            answer = await provider.text_chat(system_prompt="只从给出的已看分集资料中选直接回答问题的一处原文。截图只作辨认线索。"
                                              '身份、事实或对应关系不确定时返回 {}。否则只返回 JSON {"episode":集数,"quote":"逐字短引"}，禁止补写剧情。',
                                              prompt=json.dumps(data, ensure_ascii=False), contexts=[])
        try:
            pick = parse_object(answer.completion_text)
            n, quote = int(pick.get("episode", -1)), str(pick.get("quote") or "").strip()
        except (ValueError, TypeError):
            return fallback
        if n not in selected or not 2 <= len(quote) <= 120 or quote not in facts[n]:
            return fallback
        return f"《{title}》第 {n} 集的分集介绍提到「{quote}」"
