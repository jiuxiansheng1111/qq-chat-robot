"""Source-preserving QQ owner memory. Retrieval never crosses a conversation.

The archive stores the owner's own text and delivered replies, not complete
group prompts. Search excerpts are bounded; the original text is not truncated.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.services.owner_policy import ROOT, is_owner_event, load_owner_policy

ARCHIVE_PATH = ROOT / "data/dafeiyu/owner-memory.sqlite3"
_CONTROL = re.compile(r"^(?:NO_REPLY|\[?QQCHAT_[A-Z_]+\]?)$")
_SECRET = re.compile(
    r"(?i)(\b(?:access_token|refresh_token|authorization|password|passwd|api[_-]?key|cookie|MUSIC_U|MUSIC_A|__csrf|SESSDATA|bili_jct)\b|密码)"
    r"(\s*(?:[=:：]|是)\s*)(?:Bearer\s+)?[^\s，。；;]+"
)


def safe_text(value: str) -> str:
    # Do not retain pasted credentials or a CQ base64 payload as conversation.
    value = re.sub(r"\[CQ:[^\]]*\]", "[附件]", str(value or ""))
    value = re.sub(r"(?:data:[^\s;,]+;)?base64,[A-Za-z0-9+/=]+", "[附件数据省略]", value)
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._-]{12,}","Bearer [凭据省略]",value)
    return _SECRET.sub(lambda m: m[1] + m[2] + "[凭据省略]", value).strip()


@dataclass(frozen=True)
class MemoryOwner:
    bot: str
    scope: str
    user: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.bot, self.scope, self.user


def event_owner(event) -> MemoryOwner | None:
    if not is_owner_event(event):
        return None
    raw = getattr(getattr(event, "message_obj", None), "raw_message", {}) or {}
    if not isinstance(raw, dict) or raw.get("post_type", "message") != "message":
        return None
    bot = str(raw.get("self_id") or getattr(event, "get_self_id", lambda: "")())
    user = str(raw.get("user_id") or getattr(event, "get_sender_id", lambda: "")())
    scope = str(getattr(event, "unified_msg_origin", ""))
    if not bot or not scope or bot == user or raw.get("message_type") not in {"group", "private"}:
        return None
    sender=raw.get("sender") or {}
    if not isinstance(sender,dict) or sender.get("is_bot") is True:
        return None
    return MemoryOwner(bot, scope, user)


def message_id(event) -> str:
    raw = getattr(getattr(event, "message_obj", None), "raw_message", {}) or {}
    return str(raw.get("message_id") or getattr(getattr(event, "message_obj", None), "message_id", ""))


def incoming_text(event) -> str:
    raw = getattr(getattr(event, "message_obj", None), "raw_message", {}) or {}
    segments = raw.get("message")
    if isinstance(segments, list):
        # A quote is a reference, not something the current speaker has said.
        return safe_text("".join(str(s.get("data", {}).get("text", "")) for s in segments
                                 if isinstance(s, dict) and s.get("type") == "text"))
    return safe_text(getattr(event, "get_message_str", lambda: "")())


def _terms(text: str) -> set[str]:
    terms = set()
    for word in re.findall(r"[\wぁ-ヿ一-鿿]+", text.casefold()):
        if len(word) >= 2:
            terms.add(word)
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
    return terms


def _excerpt(text: str, query: str, size: int = 1400) -> dict:
    words = sorted(_terms(query), key=len, reverse=True)
    position = next((text.casefold().find(w) for w in words if w in text.casefold()), 0)
    start = max(0, position - 180)
    end = min(len(text), start + size)
    return {"text": text[start:end], "offset": start, "total_characters": len(text),
            "has_more": start > 0 or end < len(text)}


class OwnerMemoryArchive:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript('''
          CREATE TABLE IF NOT EXISTS owner_dialogue (
            id INTEGER PRIMARY KEY, bot TEXT NOT NULL, scope TEXT NOT NULL, owner TEXT NOT NULL,
            message_id TEXT NOT NULL, role TEXT NOT NULL, source TEXT NOT NULL,
            text TEXT NOT NULL, attachments TEXT NOT NULL, created_at TEXT NOT NULL,
            dedupe TEXT NOT NULL, UNIQUE(bot,scope,owner,dedupe));
          CREATE INDEX IF NOT EXISTS owner_dialogue_scope ON owner_dialogue(bot,scope,owner,id);
          CREATE TABLE IF NOT EXISTS owner_terms (
            entry_id INTEGER NOT NULL REFERENCES owner_dialogue(id) ON DELETE CASCADE,
            term TEXT NOT NULL, PRIMARY KEY(entry_id,term));
          CREATE INDEX IF NOT EXISTS owner_terms_lookup ON owner_terms(term,entry_id);
          CREATE TABLE IF NOT EXISTS owner_notes (
            id INTEGER PRIMARY KEY, bot TEXT NOT NULL, scope TEXT NOT NULL, owner TEXT NOT NULL,
            entry_id INTEGER NOT NULL REFERENCES owner_dialogue(id) ON DELETE CASCADE,
            category TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(bot,scope,owner,category,text));
        ''')
        return db

    def append(self, owner: MemoryOwner, *, text: str, role: str, origin_id: str,
               source="qq", attachments=(), timestamp="") -> int:
        text = safe_text(text)
        if (role not in {"owner", "assistant"} or not (text or attachments)
                or _CONTROL.fullmatch(text)):
            return 0
        if not origin_id:
            # No unstable time/content-only dedupe: different identical messages
            # are distinct experiences. Native QQ events always carry an ID.
            return 0
        digest = hashlib.sha256((role + ":" + origin_id + (":" + text if role == "assistant" else "")).encode()).hexdigest()
        now = timestamp or datetime.now(UTC).isoformat()
        with self._connect() as db:
            cursor = db.execute('''INSERT OR IGNORE INTO owner_dialogue
                (bot,scope,owner,message_id,role,source,text,attachments,created_at,dedupe)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (*owner.key, origin_id, role, source, text, json.dumps(list(attachments), ensure_ascii=False), now, digest))
            if not cursor.rowcount:
                return int(db.execute("SELECT id FROM owner_dialogue WHERE bot=? AND scope=? AND owner=? AND dedupe=?",
                                      (*owner.key, digest)).fetchone()[0])
            entry_id = cursor.lastrowid
            db.executemany("INSERT OR IGNORE INTO owner_terms VALUES(?,?)", ((entry_id, t) for t in _terms(text)))
            return int(entry_id)

    def search(self, owner: MemoryOwner, query="", limit=6) -> list[dict]:
        if not self.path.exists():
            return []
        limit = min(12, max(1, int(limit)))
        terms = sorted(_terms(query), key=len, reverse=True)[:64]
        with self._connect() as db:
            if terms:
                marks = ",".join("?" for _ in terms)
                rows = db.execute(f'''SELECT d.*, COUNT(t.term) AS score FROM owner_dialogue d
                    JOIN owner_terms t ON t.entry_id=d.id WHERE d.bot=? AND d.scope=? AND d.owner=?
                    AND t.term IN ({marks}) GROUP BY d.id ORDER BY score DESC,d.id DESC LIMIT ?''',
                    (*owner.key, *terms, limit)).fetchall()
            elif query.strip():
                # Single-character queries still work without wildcard semantics.
                rows = db.execute('''SELECT * FROM owner_dialogue WHERE bot=? AND scope=? AND owner=?
                    AND instr(lower(text),?)>0 ORDER BY id DESC LIMIT ?''', (*owner.key, query.casefold(), limit)).fetchall()
            else:
                rows = db.execute('''SELECT * FROM owner_dialogue WHERE bot=? AND scope=? AND owner=?
                    ORDER BY id DESC LIMIT ?''', (*owner.key, limit)).fetchall()
        return [{"id": r["id"], "role": r["role"], "source": r["source"],
                 "created_at": r["created_at"], "message_id": r["message_id"],
                 "attachments": json.loads(r["attachments"]), **_excerpt(r["text"], query)} for r in rows]

    def read(self, owner: MemoryOwner, entry_id: int, offset=0, size=5000) -> dict:
        if not self.path.exists():
            return {"status": "not_found"}
        with self._connect() as db:
            row = db.execute("SELECT * FROM owner_dialogue WHERE bot=? AND scope=? AND owner=? AND id=?",
                             (*owner.key, int(entry_id))).fetchone()
        if row is None:
            return {"status": "not_found"}
        offset, size = max(0, int(offset)), min(12000, max(100, int(size)))
        return {"status": "found", "id": row["id"], "role": row["role"], "source": row["source"],
                "created_at": row["created_at"], "text": row["text"][offset:offset+size],
                "offset": offset, "total_characters": len(row["text"]),
                "has_more": offset+size < len(row["text"])}

    def note(self, owner: MemoryOwner, entry_id: int, content: str, category="agreement") -> bool:
        if category not in {"agreement", "music_preference"}:
            return False
        with self._connect() as db:
            row = db.execute("SELECT text FROM owner_dialogue WHERE bot=? AND scope=? AND owner=? AND id=? AND role='owner'",
                             (*owner.key, int(entry_id))).fetchone()
            # Only literal owner statements can be pinned. No invented biography.
            if not row or not content.strip() or safe_text(content) != content or content not in row[0]:
                return False
            db.execute('''INSERT OR IGNORE INTO owner_notes
                (bot,scope,owner,entry_id,category,text,created_at) VALUES(?,?,?,?,?,?,?)''',
                (*owner.key, entry_id, category, content, datetime.now(UTC).isoformat()))
        return True

    def notes(self, owner: MemoryOwner, *, category="", query="", limit=12, offset=0) -> list[dict]:
        if not self.path.exists():
            return []
        sql, args = "SELECT id,entry_id,category,text,created_at FROM owner_notes WHERE bot=? AND scope=? AND owner=?", list(owner.key)
        if category:
            sql += " AND category=?"
            args.append(category)
        if query:
            sql += " AND instr(lower(text),?)>0"
            args.append(query.casefold())
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        args += [min(100, max(1, int(limit))), max(0, int(offset))]
        with self._connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def forget(self, owner: MemoryOwner, entry_id=0, *, all_records=False) -> int:
        if not self.path.exists():
            return 0
        sql, args = "DELETE FROM owner_dialogue WHERE bot=? AND scope=? AND owner=?", list(owner.key)
        if not all_records:
            sql += " AND id=?"
            args.append(int(entry_id))
        with self._connect() as db:
            return max(0, db.execute(sql, args).rowcount)


def archive() -> OwnerMemoryArchive:
    return OwnerMemoryArchive(ARCHIVE_PATH)


def record_incoming(event) -> int:
    owner = event_owner(event)
    if owner is None or not load_owner_policy().archive_enabled:
        return 0
    raw = event.message_obj.raw_message
    attachments = []
    for segment in raw.get("message", []) if isinstance(raw.get("message"), list) else []:
        kind = segment.get("type") if isinstance(segment, dict) else ""
        if kind and kind != "text":
            descriptor = {"type": kind, "media_stored": False}
            if kind == "reply":
                descriptor["quoted_message_id"] = str(segment.get("data", {}).get("id", ""))
            attachments.append(descriptor)
    stamp = ""
    try:
        if raw.get("time"):
            stamp = datetime.fromtimestamp(float(raw["time"]), UTC).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return archive().append(owner, text=incoming_text(event), role="owner", origin_id=message_id(event),
                            attachments=attachments, timestamp=stamp)


def record_assistant(event, text: str, source="chat") -> bool:
    """Call only after a successful actual send (including background results)."""
    owner = event_owner(event)
    if owner is None or not load_owner_policy().archive_enabled:
        return False
    return bool(archive().append(owner, text=text, role="assistant", origin_id=message_id(event), source=source))


def remember_statement(event, content: str, category="agreement") -> dict:
    owner = event_owner(event)
    if owner is None:
        return {"status": "denied"}
    if category == "music_preference" and not load_owner_policy().music_profile_enabled:
        return {"status": "disabled"}
    if category == "agreement" and not re.search(r"记住|记下来|记一下|长期|以后|一直|今后|别忘", incoming_text(event)):
        return {"status": "not_explicit", "message": "本轮未明确要求固定保存，原话仍可按需检索"}
    entry_id = record_incoming(event)
    saved = entry_id and archive().note(owner, entry_id, content, category)
    return {"status": "saved" if saved else "not_saved", "source_entry_id": entry_id,
            "message": "只接受本轮主人亲口表达的原文，不根据播放次数推断喜欢"}


def forget_request_allowed(text: str,all_current=False) -> bool:
    # Quoted commands and "don't forget me" are not deletion requests.
    text=re.sub(r'```[\s\S]*?```|`[^`]*`|“[^”]*”|「[^」]*」|"[^"\n]*"',"",text)
    if re.search(r"(?:别|不要|不许|请勿|无需|不能|不会|没有).{0,8}(?:忘|删|清)",text):
        return False
    if not re.search(r"忘记|忘掉|删除.*记忆|清除.*记忆|清空.*记忆",text):
        return False
    if not all_current:
        return True
    if re.search(r"全部|所有|清空",text):
        return True
    return bool(re.fullmatch(r"\s*(?:(?:大肥鱼|鲸鱼娘|小鲸鱼|肥鱼|蠢鱼)[，,\s]*)?(?:请)?"
        r"(?:忘记我|忘掉我|清除长期记忆)[吧啊呀\s，,。！!]*",text))
