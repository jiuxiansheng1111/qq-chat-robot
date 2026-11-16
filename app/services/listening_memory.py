"""Explicitly authorized music experiences, isolated by QQ conversation and user."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class ListeningMemory:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('''CREATE TABLE IF NOT EXISTS music_listens (
            scope TEXT NOT NULL, listener TEXT NOT NULL, song_id TEXT NOT NULL,
            title TEXT NOT NULL, artists TEXT NOT NULL, aliases TEXT NOT NULL,
            heard_at TEXT NOT NULL, listen_count INTEGER NOT NULL DEFAULT 1,
            evidence TEXT NOT NULL, reflection TEXT NOT NULL,
            PRIMARY KEY(scope, listener, song_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS music_listen_events (
            id INTEGER PRIMARY KEY, scope TEXT NOT NULL, listener TEXT NOT NULL,
            song_id TEXT NOT NULL, title TEXT NOT NULL, artists TEXT NOT NULL,
            heard_at TEXT NOT NULL, evidence TEXT NOT NULL, reflection TEXT NOT NULL)''')
        db.execute("CREATE INDEX IF NOT EXISTS music_events_owner ON music_listen_events(scope,listener,id)")
        return db

    def remember(self, owner, track: dict, evidence: dict, reflection: str):
        # This API is called only after actual audio analysis succeeds.
        if not evidence or not re.fullmatch(r"(?:[0-9]{1,20}|audio:[0-9a-f]{64})", str(track.get("song_id", ""))):
            raise ValueError("只有实际听取且具有有效曲目编号的歌曲可以保存")
        encoded=json.dumps(evidence,ensure_ascii=False)
        heard_at = datetime.now(UTC).isoformat()
        reflection = re.sub(r"&&[^&\n]+&&", "", reflection)
        with self._connect() as db:
            db.execute('''INSERT INTO music_listens VALUES(?,?,?,?,?,?,?,1,?,?)
                ON CONFLICT(scope,listener,song_id) DO UPDATE SET
                title=excluded.title, artists=excluded.artists, aliases=excluded.aliases,
                heard_at=excluded.heard_at, listen_count=music_listens.listen_count+1,
                evidence=excluded.evidence, reflection=excluded.reflection''',
                (*owner, str(track["song_id"]), str(track.get("title", ""))[:200],
                 str(track.get("artist", ""))[:300], json.dumps([str(x)[:60] for x in track.get("aliases",[])[:12]],ensure_ascii=False),
                 heard_at,encoded,reflection))
            db.execute('''INSERT INTO music_listen_events
                (scope,listener,song_id,title,artists,heard_at,evidence,reflection)
                VALUES(?,?,?,?,?,?,?,?)''',
                (*owner,str(track["song_id"]),str(track.get("title","")),str(track.get("artist","")),
                 heard_at,encoded,reflection))

    def timeline(self, owner, song_id="", limit=6, offset=0):
        if not self.path.exists():
            return []
        sql, args = "SELECT * FROM music_listen_events WHERE scope=? AND listener=?", list(owner)
        if song_id:
            sql += " AND song_id=?"
            args.append(str(song_id))
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        args += [min(20,max(1,int(limit))),max(0,int(offset))]
        with self._connect() as db:
            db.row_factory=sqlite3.Row
            return [dict(row) for row in db.execute(sql,args)]

    def read_event(self,owner,entry_id,offset=0,size=5000):
        if not self.path.exists():
            return {"status":"not_found"}
        with self._connect() as db:
            db.row_factory=sqlite3.Row
            row=db.execute("SELECT * FROM music_listen_events WHERE scope=? AND listener=? AND id=?",
                           (*owner,int(entry_id))).fetchone()
        if row is None:
            return {"status":"not_found"}
        offset,size=max(0,int(offset)),min(12000,max(100,int(size)))
        text=json.dumps({"evidence":json.loads(row["evidence"]),"whale_reflection":row["reflection"]},ensure_ascii=False)
        return {"status":"found","id":row["id"],"song_id":row["song_id"],"heard_at":row["heard_at"],
                "text":text[offset:offset+size],"offset":offset,"total_characters":len(text),
                "has_more":offset+size<len(text)}

    def recall(self, owner, query="", limit=6):
        if not self.path.exists():
            return []
        limit = min(12, max(1, int(limit)))
        # Always filter the owner in SQL before ranking; no cross-user recall.
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM music_listens WHERE scope=? AND listener=? ORDER BY heard_at DESC",
                              tuple(owner)).fetchall()
        query_text=str(query).casefold()
        words = [w.casefold() for w in re.findall(r"[\wぁ-ヿ一-鿿]{2,}", str(query))]
        def rank(row):
            try:aliases=json.loads(row["aliases"])
            except ValueError:aliases=[]
            names=[str(n).casefold() for n in (row["title"],*row["artists"].split("/"),*aliases) if n]
            return (sum(n in query_text or any(w in n for w in words) for n in names),row["heard_at"])
        return [{k: row[k] for k in ("song_id", "title", "artists", "aliases", "heard_at", "listen_count", "reflection")}
                for row in sorted(rows, key=rank, reverse=True)[:limit]]

    def summaries(self,owner,query="",limit=6,budget=8000):
        result=[]
        remaining=max(500,int(budget))
        for row in self.recall(owner,query,limit):
            item=dict(row)
            reflection=str(item.pop("reflection",""))
            base=len(json.dumps(item,ensure_ascii=False))+100
            if base>=remaining:
                break
            size=min(1200,remaining-base)
            item["reflection"]=reflection[:size]
            item["reflection_has_more"]=len(reflection)>size
            result.append(item)
            remaining-=len(json.dumps(item,ensure_ascii=False))
        return result

    def forget(self, owner, song_id=""):
        if not self.path.exists():
            return 0
        with self._connect() as db:
            sql = "DELETE FROM music_listens WHERE scope=? AND listener=?"
            args = tuple(owner)
            if song_id:
                sql += " AND song_id=?"
                args += (str(song_id),)
            deleted=db.execute(sql, args).rowcount
            db.execute(sql.replace("music_listens","music_listen_events"),args)
            return deleted
