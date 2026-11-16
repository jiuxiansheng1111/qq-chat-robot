"""One explicit QQ owner identity for durable chat and music memory."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "data/dafeiyu/owner-memory-config.json"


@dataclass(frozen=True)
class OwnerPolicy:
    enabled: bool = False
    owner_id: str = ""
    owner_name: str = ""
    music_profile_enabled: bool = False
    archive_enabled: bool = True

    def matches(self, user_id) -> bool:
        return bool(self.enabled and self.owner_id and str(user_id or "") == self.owner_id)


@lru_cache(maxsize=8)
def _read(path: str, modified_ns: int, size: int) -> OwnerPolicy:
    if size > 16000:
        return OwnerPolicy()
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        owner = str(value.get("owner_id", ""))
        if not re.fullmatch(r"[0-9]{5,20}", owner):
            return OwnerPolicy()
        return OwnerPolicy(value.get("enabled") is True, owner,
                           str(value.get("owner_name", ""))[:80],
                           value.get("music_profile_enabled") is True,
                           value.get("archive_owner_private_and_group", True) is True)
    except (OSError, ValueError, TypeError, AttributeError):
        return OwnerPolicy()


def load_owner_policy() -> OwnerPolicy:
    try:
        stat = POLICY_PATH.stat()
        return _read(str(POLICY_PATH.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return OwnerPolicy()


def is_owner_id(user_id) -> bool:
    return load_owner_policy().matches(user_id)


def is_owner_event(event) -> bool:
    platform = str(getattr(event, "get_platform_name", lambda: "")()).casefold()
    if platform not in {"aiocqhttp", "onebot", "onebot_v11"}:
        return False
    raw = getattr(getattr(event, "message_obj", None), "raw_message", {}) or {}
    sender = raw.get("user_id") if isinstance(raw, dict) else None
    if not sender:
        sender = getattr(event, "get_sender_id", lambda: "")()
    return is_owner_id(sender)
