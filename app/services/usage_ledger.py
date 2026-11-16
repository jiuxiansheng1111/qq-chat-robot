"""Numerical Responses usage only; unknown counters stay NULL, never guessed."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from app.services.owner_policy import ROOT, is_owner_event

LEDGER_PATH = ROOT / "data/dafeiyu/token-usage.sqlite3"
_CONTEXT = ContextVar("qqchat_usage_context", default=None)


def _hash(value) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def bind_event(event, purpose="chat") -> dict:
    from app.services.qq_context_policy import bind_context
    bind_context(event)
    # No raw account, room IDs or conversation text in the usage database.
    raw = getattr(getattr(event, "message_obj", None), "raw_message", {}) or {}
    scope = str(getattr(event, "unified_msg_origin", "")) + ":" + str(raw.get("self_id", ""))
    token = str(raw.get("message_id") or getattr(event, "_qqchat_usage_turn", "") or uuid.uuid4().hex)
    event._qqchat_usage_turn = token
    value = {"scope_hash": _hash(scope), "turn_hash": _hash(scope + ":" + token),
             "owner": int(is_owner_event(event)), "purpose": purpose}
    _CONTEXT.set(value)
    return value


@contextmanager
def usage_purpose(purpose: str):
    value = dict(_CONTEXT.get() or {})
    value["purpose"] = purpose
    token = _CONTEXT.set(value)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def _counter(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _label(value, default="unknown"):
    value = str(value or "")
    return value[:100] if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", value) else default


class UsageLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.executescript('''CREATE TABLE IF NOT EXISTS usage_calls (
            id INTEGER PRIMARY KEY, response_key TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
            scope_hash TEXT NOT NULL, turn_hash TEXT NOT NULL, is_owner INTEGER NOT NULL,
            purpose TEXT NOT NULL, status TEXT NOT NULL, error_type TEXT NOT NULL,
            elapsed_ms INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
            cached_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER,
            total_tokens INTEGER);
            CREATE INDEX IF NOT EXISTS usage_owner_scope ON usage_calls(is_owner,scope_hash,created_at);''')
        return db

    def record(self, response: dict | None, *, provider: str, model: str,
               elapsed_ms=0, error_type="", context=None) -> None:
        context = dict(_CONTEXT.get() or {}) if context is None else context
        response = response if isinstance(response, dict) else {}
        usage = response.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        inputs, outputs = usage.get("input_tokens_details") or {}, usage.get("output_tokens_details") or {}
        inputs = inputs if isinstance(inputs, dict) else {}
        outputs = outputs if isinstance(outputs, dict) else {}
        rid = str(response.get("id", ""))
        response_key = _hash(provider + ":" + rid) if rid else uuid.uuid4().hex
        with self._connect() as db:
            db.execute('''INSERT OR IGNORE INTO usage_calls VALUES
                (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (response_key, datetime.now(UTC).isoformat(), _label(provider), _label(model),
                 context.get("scope_hash", ""), context.get("turn_hash", ""), int(bool(context.get("owner"))),
                 _label(context.get("purpose", "unattributed")), "failed" if error_type else _label(response.get("status", "completed")),
                 _label(error_type, "") if error_type else "", max(0, int(elapsed_ms)),
                 _counter(usage.get("input_tokens")), _counter(usage.get("output_tokens")),
                 _counter(inputs.get("cached_tokens")), _counter(inputs.get("cache_write_tokens")),
                 _counter(outputs.get("reasoning_tokens")), _counter(usage.get("total_tokens"))))

    def report(self, *, scope_hash="", owner_only=True) -> dict:
        if not self.path.exists():
            return {"calls": 0, "unknown_usage_calls": 0, "actual_total_tokens": None}
        sql, args = "SELECT * FROM usage_calls WHERE 1=1", []
        if owner_only:
            sql += " AND is_owner=1"
        if scope_hash:
            sql += " AND scope_hash=?"
            args.append(scope_hash)
        with self._connect() as db:
            rows = db.execute(sql, args).fetchall()
        counters = ("input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens", "reasoning_tokens", "total_tokens")
        # A partial known sum is explicitly labelled with coverage, not presented
        # as an entire bill. Cached tokens are a subset, never added twice.
        return {"calls": len(rows), "unknown_usage_calls": sum(r["total_tokens"] is None for r in rows),
                "failed_calls": sum(r["status"] == "failed" for r in rows),
                "known_sums": {k: sum(r[k] for r in rows if r[k] is not None) if any(r[k] is not None for r in rows) else None for k in counters},
                "coverage": {k: sum(r[k] is not None for r in rows) for k in counters},
                "note": "提供方实际返回的用量，缓存为输入子集，缺失值未知，不换算订阅额度或费用"}


def ledger() -> UsageLedger:
    return UsageLedger(LEDGER_PATH)
