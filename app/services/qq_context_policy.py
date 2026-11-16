"""Request-local QQ context limit without mutating a shared Bilibili provider."""
from __future__ import annotations

import json
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from app.services.owner_policy import ROOT

_QQ_CONTEXT = ContextVar("qqchat_context_is_qq", default=False)
POLICY_PATH = ROOT / "data/dafeiyu/qq-context-policy.json"


def bind_context(event):
    _QQ_CONTEXT.set(str(getattr(event,"get_platform_name",lambda:"")()).casefold()
                    in {"aiocqhttp","onebot","onebot_v11"})


@lru_cache(maxsize=8)
def _window(path,mtime,size):
    if size>16000:
        return 0
    try:
        policy=json.loads(Path(path).read_text(encoding="utf-8-sig"))
        limit=policy.get("max_context_tokens",0)
        verified=policy.get("verified_default_context_window",0)
        if policy.get("enabled") is True and policy.get("model")=="gpt-6-luna" and isinstance(limit,int) and isinstance(verified,int):
            return min(limit,verified) if 16000<=limit<=300000 else 0
    except (OSError,ValueError,TypeError,AttributeError):
        pass
    return 0


def qq_window():
    try:
        stat=POLICY_PATH.stat()
        return _window(str(POLICY_PATH.resolve()),stat.st_mtime_ns,stat.st_size)
    except OSError:
        return 0


class ScopedProviderConfig(dict):
    """AstrBot shares Provider objects across profiles.

    Only the native runner's context-limit lookup is overridden for an active
    QQ task. The underlying configuration and other platforms remain intact.
    """
    def get(self,key,default=None):
        if key=="max_context_tokens" and _QQ_CONTEXT.get() and super().get("model")=="gpt-6-luna":
            limit=qq_window()
            if limit:
                return limit
        return super().get(key,default)

    def __getitem__(self,key):
        if key=="max_context_tokens" and _QQ_CONTEXT.get() and super().get("model")=="gpt-6-luna":
            limit=qq_window()
            if limit:
                return limit
        return super().__getitem__(key)
