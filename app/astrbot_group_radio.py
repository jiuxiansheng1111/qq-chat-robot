"""A rotating group programme with actual song links and optional recorded introductions."""
from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime

from app.services.activity_notes import attached_voice
from app.services.music import search_netease_music
from app.services.social_plans import CHINA, PlanStore

_CREATE = re.compile(r"^(?:开(?:个|一期|一档)?|创建|开始)(?:群|群聊)?(?:轮值)?电台(?:[：:，, ]+(.{1,60}))?$")
_JOIN = {"加入电台","报名电台","我要当电台主播"}
_LEAVE = {"退出电台","退出电台轮值"}
_SUBMIT = re.compile(r"^电台(?:点歌|选歌|投稿)[：: ]*(.{1,140}?)(?:[；;]\s*(?:串词[：:]?)?(.{1,600}))?$",re.DOTALL)
_INTRO = re.compile(r"^(?:给电台留|电台)(?:录音|语音|录串词)[吧吗]?$")
_VIEW = {"电台状态","查看电台","看看电台节目单","电台节目单","电台回放"}
_EDITION = re.compile(r"^(?:听|看)?电台第(\d{1,6})期(?:回放|节目单)$")
_PUBLISH = {"发布这期电台","发布电台","播出这期电台","电台开播"}
_VOICE = re.compile(r"^听(?:本期|这期)?电台(?:第(\d{1,6})期)?第?(\d{1,2})段串词$")
_CANCEL = {"取消这期电台","取消电台"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS members(room TEXT, person TEXT, nick TEXT, id INTEGER PRIMARY KEY,
                                   UNIQUE(room, person));
CREATE TABLE IF NOT EXISTS episodes(room TEXT, edition INTEGER, title TEXT, dj TEXT, closed INTEGER DEFAULT 0,
                                    PRIMARY KEY(room, edition));
CREATE TABLE IF NOT EXISTS tracks(room TEXT, edition INTEGER, person TEXT, nick TEXT, song TEXT, artist TEXT,
                                  link TEXT, intro TEXT, voice BLOB, waiting REAL DEFAULT 0,
                                  PRIMARY KEY(room, edition, person));
"""


def programme(episode, rows):
    state = "已播出" if episode["closed"] else "筹备中"
    lines = [f"第{episode['edition']}期《{episode['title']}》 · {state}"]
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {row['nick']} 选《{row['song']}》 · {row['artist']}\n{row['link']}")
        if row["intro"]:
            lines.append(row["intro"])
        if row["spoken"]:
            lines.append(f"语音串词可说「听电台第{i}段串词」。")
    return "\n".join(lines)


def ordered(db, room, episode):
    # Membership can change between editions. Entry order freezes when the programme is published.
    return db.execute("""SELECT rowid, nick,song,artist,link,intro,voice IS NOT NULL AS spoken
                         FROM tracks WHERE room=? AND edition=? ORDER BY rowid""",
                      (room,episode["edition"])).fetchall()


async def send_voice(event, data):
    from astrbot.api.message_components import Record
    recording = Record.fromBase64(base64.b64encode(data).decode("ascii"))
    await event.send(event.chain_result([recording]))


class GroupRadio:
    title = "群轮值电台"

    def interested(self,key,text,addressed):
        if not key[2]: return False
        word = text.strip(" 。！!?？")
        return bool(not word or word in _JOIN | _LEAVE | _VIEW | _PUBLISH | _CANCEL
                    or any(p.fullmatch(word) for p in (_CREATE,_SUBMIT,_INTRO,_VOICE,_EDITION)))

    async def handle(self,host,event,key,text):
        if not key[2]: return False
        room, person = json.dumps(key[:3]), key[3]
        text = text.strip(" 。！!?？")
        store = PlanStore(host.runtime.project_root/"data/dafeiyu/group-radio.sqlite3",_SCHEMA)
        now = datetime.now(CHINA)
        raw = event.message_obj.raw_message
        sender = raw.get("sender") or {}
        nick = str(sender.get("card") or sender.get("nickname") or person)[:48]
        create, submit, listen = _CREATE.fullmatch(text), _SUBMIT.fullmatch(text), _VOICE.fullmatch(text)
        voice, track, opened = None, None, None
        if not text or _INTRO.fullmatch(text):
            if not text:
                if not store.path.is_file(): return False
                ready = await asyncio.to_thread(store.transaction, lambda db: db.execute("""
                    SELECT 1 FROM tracks t JOIN episodes e USING(room,edition) WHERE t.room=? AND t.person=?
                    AND e.closed=0 AND t.waiting>?""",(room,person,now.timestamp())).fetchone())
                if not ready: return False
            voice = await attached_voice(host,event)
            if not text and not voice: return False
        if submit:
            opened = await asyncio.to_thread(store.transaction, lambda db: db.execute(
                "SELECT edition FROM episodes WHERE room=? AND closed=0",(room,)).fetchone())
            if opened:
                matches = await search_netease_music(submit[1].strip(),host.runtime._core.settings)
                if matches:
                    track = matches[0]

        def change(db):
            episode = db.execute("SELECT * FROM episodes WHERE room=? ORDER BY edition DESC LIMIT 1",(room,)).fetchone()
            roster = db.execute("SELECT person,nick FROM members WHERE room=? ORDER BY id",(room,)).fetchall()
            if text in _JOIN or create and (not episode or episode["closed"]):
                if not any(p["person"] == person for p in roster) and len(roster) >= 20:
                    return "本群电台的轮值名额已经满了。"
                db.execute("""INSERT INTO members(room,person,nick) VALUES (?,?,?)
                              ON CONFLICT(room,person) DO UPDATE SET nick=excluded.nick""",(room,person,nick))
                roster = db.execute("SELECT person,nick FROM members WHERE room=? ORDER BY id",(room,)).fetchall()
                if text in _JOIN: return f"{nick} 已加入轮值。本群现在有{len(roster)}位选曲人。"
            if create:
                if episode and not episode["closed"]: return "本群已有一期在筹备，可以说「电台节目单」。"
                people = [p["person"] for p in roster]
                previous = episode["dj"] if episode else None
                dj = people[(people.index(previous)+1) % len(people)] if previous in people else people[0]
                edition = episode["edition"] + 1 if episode else 1
                db.execute("INSERT INTO episodes VALUES (?,?,?,?,0)",(room,edition,create[1] or "群轮值电台",dj))
                db.execute("DELETE FROM tracks WHERE room=? AND edition<?",(room,edition-3))
                db.execute("DELETE FROM episodes WHERE room=? AND edition<?",(room,edition-3))
                host_name = next(p["nick"] for p in roster if p["person"] == dj)
                return f"第{edition}期由{host_name}主持。大家可以说「加入电台」，再说「电台选歌 歌名；串词」。"
            if text in _LEAVE:
                if episode and not episode["closed"]:
                    db.execute("DELETE FROM tracks WHERE room=? AND edition=? AND person=?",(room,episode["edition"],person))
                    if episode["dj"] == person:
                        remaining = next((p for p in roster if p["person"] != person),None)
                        if remaining: db.execute("UPDATE episodes SET dj=? WHERE room=? AND edition=?",(remaining["person"],room,episode["edition"]))
                        else: db.execute("DELETE FROM episodes WHERE room=? AND edition=?",(room,episode["edition"]))
                db.execute("DELETE FROM members WHERE room=? AND person=?",(room,person))
                return "你已退出轮值，筹备中的个人选曲已撤回。"
            if not episode: return "本群还没有电台节目。可以说「开个群电台」。"
            old = _EDITION.fullmatch(text)
            edition_number = old[1] if old else listen[1] if listen else None
            if edition_number:
                episode = db.execute("SELECT * FROM episodes WHERE room=? AND edition=?",(room,int(edition_number))).fetchone()
                if not episode: return "没有查到这一期，可以回听本群最近四期。"
            edition = episode["edition"]
            rows = ordered(db,room,episode)
            if text in _VIEW or old:
                host_name = next((p["nick"] for p in roster if p["person"] == episode["dj"]),None)
                return programme(episode,rows) + (f"\n本期主持 · {host_name}" if host_name else "")
            if listen:
                index = int(listen[2])-1
                if not 0 <= index < len(rows) or not rows[index]["spoken"]: return "这一段没有录音串词。"
                return db.execute("SELECT voice FROM tracks WHERE rowid=?",(rows[index]["rowid"],)).fetchone()[0]
            if episode["closed"]: return "这一期已经播出。可以听回放，或新开一期。"
            if text in _CANCEL | _PUBLISH:
                if person != episode["dj"]: return "这一期由轮值主持人决定何时播出或取消。"
                if text in _CANCEL:
                    db.execute("DELETE FROM tracks WHERE room=? AND edition=?",(room,edition))
                    db.execute("DELETE FROM episodes WHERE room=? AND edition=?",(room,edition))
                    return "这期筹备已取消。"
                if not rows: return "这期还没有歌，选好歌再发布吧。"
                db.execute("UPDATE episodes SET closed=1 WHERE room=? AND edition=?",(room,edition))
                recordings = [(row["rowid"],row["nick"],row["song"],row["link"]) for row in rows if row["spoken"]]
                return programme(dict(episode,closed=1),rows), recordings
            if not any(p["person"] == person for p in roster): return "先说「加入电台」，就可以为本群选曲。"
            if submit:
                if not opened or opened["edition"] != edition: return "筹备的期数变了，请重新为当前这期选歌。"
                if not track: return "这次没找到对应曲目，可以带上歌手名再试。"
                db.execute("""INSERT INTO tracks(room,edition,person,nick,song,artist,link,intro) VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(room,edition,person) DO UPDATE SET song=excluded.song,artist=excluded.artist,
                    link=excluded.link,intro=excluded.intro,nick=excluded.nick,voice=NULL,waiting=0""",
                           (room,edition,person,nick,track.title,track.artist,track.page_url,(submit[2] or "").strip()))
                return f"给你排上了《{track.title}》 · {track.artist}。要配自己的声音，可以说「电台录串词」。"
            row = db.execute("SELECT rowid,waiting FROM tracks WHERE room=? AND edition=? AND person=?",(room,edition,person)).fetchone()
            if not row: return "先为本期选一首歌吧。"
            if voice:
                if not text and row["waiting"] <= now.timestamp(): return None
                db.execute("UPDATE tracks SET voice=?,waiting=0 WHERE rowid=?",(voice,row["rowid"]))
                return "这段录音已经放在你的曲目前面。"
            db.execute("UPDATE tracks SET waiting=? WHERE rowid=?",(now.timestamp()+120,row["rowid"]))
            return "请在两分钟内直接发自己的语音，放在这首歌前面。"

        reply = await asyncio.to_thread(store.turn,event,change,now)
        if reply is None: return False
        if isinstance(reply,tuple):
            listing, recordings = reply
            await event.send(event.plain_result(listing))
            for rowid, contributor, song, link in recordings:
                def read_voice(db, rowid=rowid):
                    return db.execute("SELECT voice FROM tracks WHERE room=? AND rowid=?",(room,rowid)).fetchone()
                saved = await asyncio.to_thread(store.transaction,read_voice)
                if saved and saved["voice"]:
                    await event.send(event.plain_result(contributor + " 的开场串词"))
                    await send_voice(event,saved["voice"])
                    await event.send(event.plain_result(f"《{song}》 {link}"))
        elif isinstance(reply,bytes): await send_voice(event,reply)
        elif reply: await event.send(event.plain_result(reply))
        return True
