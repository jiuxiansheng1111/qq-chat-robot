"""QQ entry point for optional conversational tools that run before ordinary commands."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from importlib import import_module

from app.astrbot_companion_turns import _target
from app.astrbot_group_chat import _human_message_text, _strip_name
from app.astrbot_listening import ALIASES
from app.astrbot_qq_persona import is_qq
from app.services.social_plans import scope

_LOG = logging.getLogger(__name__)
_FEATURES = {"listen_first": "ListenFirst", "card_quiz": "CardQuiz", "watchlist": "Watchlist", "corrections": "Corrections",
             "item_finder": "ItemFinder", "project_resume": "ProjectResume", "fridge": "Fridge", "dinner_split": "DinnerSplit",
             "study": "Study", "time_capsule": "TimeCapsule", "steam_library": "SteamLibrary", "group_radio": "GroupRadio",
             "mc_builder": "McBuilder", "werewolf": "Werewolf",
             "web_assistant": "WebAssistant", "desktop_control": "DesktopControl", "phone_control":"PhoneControl",
             "home_control": "HomeControl"}


def start_timers(host, slugs):
    if getattr(getattr(host, "runtime", None), "project_root", None) is not None:
        for slug in slugs:
            get_feature(host, slug).start(host)


async def quiet_study_event(host, event):
    from app.astrbot_qq_persona import is_qq
    if not is_qq(event):
        return False
    tool = getattr(host, "_conversation_features", {}).get("study")
    enabled = enabled_parts(host,event)
    if not tool or enabled is not None and "study" not in enabled:
        return False
    mid = str(getattr(getattr(event,"message_obj",None),"message_id",""))
    passive = mid.startswith("proactive:") or event.get_extra("qqchat_group_reply_reason") == "interject"
    return passive and await tool.quiet(host,event.unified_msg_origin)


def enabled_parts(host, event):
    return {p.slug for p in host.parts(event)} if hasattr(host, "parts") else None


def context_for(host, event):
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if not isinstance(raw, dict) or not is_qq(event) or raw.get("post_type") != "message" or raw.get("message_type") not in {"group", "private"}:
        return None
    if not raw.get("self_id") or not raw.get("user_id") or host._get_group_router().ignored_sender(event):
        return None
    text, named = _strip_name(_human_message_text(event).strip(), ALIASES)
    visible, addressed = _target(event, raw, False, named)
    if not visible or (raw.get("message_type") == "group" and not host._get_group_router().allows_companion_turn(event, addressed)):
        return None
    return scope(event), text.strip(), addressed


def get_feature(host, slug):
    features = getattr(host, "_conversation_features", None)
    if features is None:
        features = host._conversation_features = {}
    if slug not in features:
        module = import_module("app.astrbot_" + slug)
        features[slug] = getattr(module, _FEATURES[slug])()
    return features[slug]


def routes(host, event):
    if not getattr(host, "runtime", None):
        return []
    context = context_for(host, event)
    if context is None:
        return []
    key, text, addressed = context
    mid = event.message_obj.raw_message.get("message_id")
    if mid and getattr(host, "_conversation_receipts", {}).get((*key, str(mid)), 0) > time.monotonic():
        return [(None, key, text)]
    enabled = enabled_parts(host, event)
    found = []
    for slug in _FEATURES:
        if enabled is not None and slug not in enabled:
            continue
        tool = get_feature(host, slug)
        if tool.interested(key, text, addressed):
            found.append((tool, key, text))
    return found


async def handle(host, event):
    if getattr(event, "is_stopped", lambda: False)():
        return False
    choices = routes(host, event)
    if not choices or not await host._legacy_event_allowed(event.message_obj.raw_message):
        return False
    _, key, text = choices[0]
    mid = event.message_obj.raw_message.get("message_id")
    receipt = (*key, str(mid))
    seen = getattr(host, "_conversation_receipts", None)
    if seen is None:
        seen = host._conversation_receipts = OrderedDict()
    now = time.monotonic()
    while seen and next(iter(seen.values())) <= now:
        seen.popitem(last=False)
    if (mid and receipt in seen) or choices[0][0] is None:
        event.should_call_llm(False)
        event.stop_event()
        return True
    if mid:
        if len(seen) >= 1024:
            seen.popitem(last=False)
        seen[receipt] = now + 900
    for tool, key, text in choices:
        try:
            used = await tool.handle(host, event, key, text)
        except Exception as exc:  # noqa: BLE001 -- isolate adapter or provider failures from the normal chat pipeline.
            _LOG.warning("QQ 对话功能未完成 feature=%s error=%s", type(tool).__name__, type(exc).__name__)
            from app.astrbot_failure_reply import Failure, describe_failure

            await event.send(event.plain_result(await describe_failure(host.context, event, Failure(tool.title, "unavailable"))))
            used = True
        if used:
            event.should_call_llm(False)
            event.stop_event()
            return True
    if mid:
        seen.pop(receipt, None)
    return False


def remember_lookup(host, event, domain, name, **options):
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if not name or not is_qq(event) or not isinstance(raw, dict) or not raw.get("self_id") or not raw.get("user_id") or not callable(getattr(event, "get_platform_id", None)):
        return
    enabled = enabled_parts(host, event)
    if enabled is None or "corrections" in enabled:
        if domain == "cards" and "retry" not in options:
            if enabled is None:
                from app.astrbot_card_lookup import _retry
                options["retry"] = _retry
            elif "cards" in enabled:
                options["retry"] = next(p for p in host.parts(event) if p.slug == "cards").retry_lookup
            else:
                return
        tool = get_feature(host, "corrections")
        tool.record(event, domain, name, **options)


async def release(host, slug=None):
    features = getattr(host, "_conversation_features", {})
    for name in [slug] if slug else list(features):
        tool = features.pop(name, None)
        if tool and hasattr(tool, "close"):
            await tool.close()


async def observe_image(host, refs, instruction):
    from app.astrbot_vision_provider import PROVIDER_ID

    provider = host.context.get_provider_by_id(PROVIDER_ID)
    if not provider:
        raise ValueError("QQ vision unavailable")
    async with asyncio.timeout(65):
        result = await provider.text_chat(prompt=instruction, image_urls=refs)
    return str(result.completion_text or "").strip()
