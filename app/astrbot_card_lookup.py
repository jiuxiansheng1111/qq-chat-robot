"""QQ card previews and player-confirmed details, scoped to the requesting player."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from app.astrbot_hearthstone import _scope, query, service
from app.astrbot_qq_persona import is_qq
from app.astrbot_singing import _addressed_text
from app.services.hearthstone.cards import card_name_key, parse_card_lookup
from app.services.hearthstone.decks import CLASSES, MODES

_LOG = logging.getLogger(__name__)
_NUMBERS = "一二三四五六七八九十"
_PICK = re.compile(r"(?:确认查看|确认|查看|选|看)?\s*第?\s*([0-9]{1,2}|[一二两三四五六七八九十])\s*(?:张|个)?")
_TTL = 300


@dataclass
class CardChoices:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    expires: float = 0
    cards: list[dict] = field(default_factory=list)


def _choice(text):
    text = text.strip().rstrip("。！？!")
    command = re.match(r"^/?查卡确认\s*[:：]?\s*", text)
    if command:
        text = text[command.end():]
    if text in {"确认", "就这张", "就是这张"} or command and not text:
        return ""
    index = _PICK.fullmatch(text)
    if index:
        number = index[1].replace("两", "二")
        return int(number) if number.isdecimal() else _NUMBERS.index(number) + 1
    if command:
        return text
    name = re.fullmatch(r"确认(?:查看)?\s+(.{1,100})", text)
    return name[1] if name else None


def _formats(card):
    return "、".join(label for mode, label in MODES.items() if card.get(mode)) or "不在标准或狂野构筑卡池"


def _identity(card):
    return " · ".join((card.get("set") or "系列未收录", _formats(card)))


def _previews(result):
    cards = result["cards"]
    lines = ["卡名相近的有这几张：" if result.get("similar") else "查到这些卡：" if len(cards) > 1 else "你要看这张吗？"]
    for index, card in enumerate(cards, 1):
        effect = " ".join(card.get("effect", "").split()) or "无卡牌效果文字"
        if len(effect) > 54:
            effect = effect[:53] + "…"
        lines.append(f"{index}. {card['name']}（{card['mana']}费，{_identity(card)}）\n   {effect}")
    if result.get("total", 0) > len(cards):
        lines.append(f"共 {result['total']} 条匹配，可补全卡名缩小范围。")
    lines.append("回复“确认”或“1”看卡面和详情。" if len(cards) == 1 else "回复序号看那一张的卡面和详情。")
    return "\n".join(lines)


def _details(card):
    classes = "／".join(CLASSES.get(c, c) for c in re.split(r"[,;/|]", card["class"]) if c)
    stats = [f"{card['mana']}费", classes, card.get("rarity", "") + card.get("type", "")]
    if card["type"] == "随从":
        stats.append(f"{card.get('attack', 0)}/{card.get('health', 0)}")
    elif card["type"] == "武器":
        stats.append(f"{card.get('attack', 0)}攻/{card.get('health', 0)}耐久")
    lines = [card["name"], " · ".join(s for s in stats if s),
             "效果：" + (card.get("effect") or "无卡牌效果文字"),
             "所属系列：" + (card.get("set") or "未收录"), "构筑模式：" + _formats(card)]
    runes = card.get("runes") or {}
    required = [f"{label}{runes[key]}" for key, label in (("blood", "鲜血"), ("frost", "冰霜"), ("unholy", "邪恶"))
                if runes.get(key)]
    if required:
        lines.append("符文：" + "、".join(required))
    if card.get("collectible") and card.get("dust"):
        lines.append(f"普通版本合成：{card['dust']} 奥术之尘")
    return "\n".join(lines)


async def _show_card(plugin, event, card):
    from astrbot.api.message_components import Image, Plain

    text = _details(card)
    try:
        async with asyncio.timeout(12):
            art = await service(plugin).card_image(card)
    except (ValueError, OSError, KeyError, TypeError, TimeoutError, httpx.HTTPError) as exc:
        _LOG.warning("炉石卡面读取失败 dbf=%s error=%s", card["dbf_id"], type(exc).__name__)
        page = f"https://hearthstone.blizzard.com/zh-tw/cards/{card['dbf_id']}/"
        await event.send(event.plain_result(f"{text}\n卡面暂时没加载出来，回复“确认”可以重试。卡牌页：{page}"))
        return False
    await event.send(event.chain_result([Plain(text + "\n"), Image.fromBytes(art)]))
    return True


async def _search(plugin, event, state, mode, name):
    state.cards = []
    state.expires = 0
    result = await query(plugin, event, action="card", mode=mode, query=name)
    if result["status"] == "ok" and result.get("cards"):
        await event.send(event.plain_result(_previews(result)))
        state.cards = result["cards"]
        state.expires = time.monotonic() + _TTL
    elif result["status"] == "not_found":
        area = MODES.get(mode, "炉石")
        await event.send(event.plain_result(f"{area}卡库里没找到“{name}”，换一段卡名或英文名试试。"))
    elif result["status"] != "denied":
        if result["status"] in {"unavailable", "invalid_or_unavailable"} and name:
            from app.astrbot_failure_reply import Failure, describe_failure
            message = await describe_failure(plugin.context, event, Failure("炉石查卡", "unavailable"))
        else:
            message = result.get("message") or "想查哪张炉石卡牌？"
        await event.send(event.plain_result(message))


async def _confirm(plugin, event, state, pick):
    cards = state.cards
    if not cards or state.expires <= time.monotonic():
        await event.send(event.plain_result("请先发“查卡 卡名”，再从这次的结果里选。"))
        return
    if isinstance(pick, int):
        chosen = cards[pick - 1:pick] if 1 <= pick <= len(cards) else []
    elif pick:
        key = card_name_key(pick)
        chosen = [c for c in cards if key in {card_name_key(c["name"]), card_name_key(c.get("english_name", ""))}]
    else:
        chosen = cards
    if len(chosen) != 1:
        await event.send(event.plain_result(f"请回复刚才列表里的序号（1～{len(cards)}），我会发对应版本的卡面。"))
        return
    if await _show_card(plugin, event, chosen[0]):
        state.cards = []
        state.expires = 0
    else:
        state.cards = chosen
        state.expires = time.monotonic() + _TTL


async def handle(plugin, event, *, command=False):
    if not is_qq(event) or plugin.runtime is None or getattr(event, "is_stopped", lambda: False)():
        return False
    text, addressed = _addressed_text(event)
    if not text:
        return False
    lookup = parse_card_lookup(text)
    pick = None if lookup is not None else _choice(text)
    if lookup is None and pick is None:
        return False
    if lookup is not None and not (addressed or command or text.startswith("/")):
        return False
    key = _scope(event)
    sessions = getattr(plugin, "_card_lookups", None)
    if sessions is None:
        sessions = plugin._card_lookups = {}
    now = time.monotonic()
    for scope, old in list(sessions.items()):
        if old.expires <= now and not old.lock.locked():
            del sessions[scope]
    state = sessions.get(key)
    if lookup is None and state is None and not text.lstrip("/").startswith("查卡确认"):
        return False
    raw = getattr(event.message_obj, "raw_message", None)
    if not await plugin._legacy_event_allowed(raw):
        return False
    event.should_call_llm(False)
    try:
        # No await between looking up and reserving a slot: parallel turns in
        # the same conversation must never select from different option lists.
        state = sessions.get(key)
        if state is None:
            if lookup is None:
                await event.send(event.plain_result("请先发“查卡 卡名”，再从这次的结果里选。"))
                return True
            if len(sessions) >= 128:
                idle = [scope for scope, item in sessions.items() if not item.lock.locked()]
                if not idle:
                    await event.send(event.plain_result("查卡的人有点多，请稍后再查一次。"))
                    return True
                del sessions[min(idle, key=lambda scope: sessions[scope].expires)]
            state = sessions[key] = CardChoices()
        async with state.lock:
            if lookup is not None:
                await _search(plugin, event, state, *lookup)
            else:
                await _confirm(plugin, event, state, pick)
        return True
    finally:
        event.stop_event()
