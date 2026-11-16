"""Bounded deckstring decoding and analysis using current card records.

Protocol reference: HearthSim/python-hearthstone, hearthstone/deckstrings.py.
No deck is created or uploaded to Yingdi when decoding a user's code.
"""
from __future__ import annotations

import base64
import binascii
import html
import re
from collections import Counter

MODES = {"standard": "标准", "wild": "狂野"}
CLASSES = {
    "Druid": "德鲁伊", "Hunter": "猎人", "Mage": "法师", "Paladin": "圣骑士",
    "Priest": "牧师", "Rogue": "潜行者", "Shaman": "萨满", "Warlock": "术士",
    "Warrior": "战士", "Demonhunter": "恶魔猎手", "Deathknight": "死亡骑士",
    "Neutral": "中立",
}
CODE = re.compile(r"(?<![A-Za-z0-9+/])AA[A-Za-z0-9+/]{18,1500}={0,2}(?![A-Za-z0-9+/=])")
ROLES = {
    "抽牌": r"抽.{0,8}牌|抽取|抽一张|抽两张|抽三张",
    "解场": r"消灭|造成.{0,12}伤害|移除|沉默|变形|移回.*手牌",
    "群体解场": r"所有.{0,16}(?:伤害|消灭)|消灭所有",
    "回复护甲": r"恢复.{0,12}生命|护甲|吸血",
    "站场": r"召唤|复活|亡语|突袭|圣盾|嘲讽",
    "增益": r"\+\d|获得.{0,12}攻击力|使你的.*获得",
    "资源": r"发现|置入.*手牌|复制|获取|获得.*张",
    "减费跳费": r"法力水晶|法力值消耗.*减少|法力值消耗为",
    "直伤": r"敌方英雄|敌人造成",
}


def clean(value, limit=800):
    text = html.unescape(re.sub(r"<[^>]*>", "", str(value or "")))
    return re.sub(r"[\x00-\x08\x0b-\x1f]", "", text).replace("$", "")[:limit].strip()


def card_record(raw):
    """Keep only public game data; author/user identifiers never enter the model."""
    return {
        "id": int(raw["id"]), "dbf_id": int(raw["gameid"]),
        "name": clean(raw.get("cname"), 80), "english_name": clean(raw.get("ename"), 100),
        "mana": int(raw.get("mana", 0)), "type": clean(raw.get("clazz"), 20),
        "class": clean(raw.get("factions") or raw.get("faction"), 100),
        "rarity": clean(raw.get("rarity"), 20), "effect": clean(raw.get("rule")),
        "attack": int(raw.get("attack", 0)), "health": int(raw.get("hp", 0)),
        "tribe_or_school": clean(raw.get("race"), 50),
        "set": clean(raw.get("seriesName"), 80), "set_code": clean(raw.get("seriesAbbr"), 30),
        "standard": raw.get("standard") == 1, "wild": raw.get("wild") == 1,
        "collectible": raw.get("deckable") == 1,
        "dust": max(0, int(raw.get("forge", 0))),
        "runes": {k: max(0, int((raw.get("runeCost") or {}).get(k, 0)))
                  for k in ("blood", "frost", "unholy")},
        "updated": raw.get("updated"),
        "image_url": raw.get("img") or "",
        "url": "https://www.iyingdi.com/web/tools/hearthstone/cards/" + str(int(raw["id"])),
    }


def roles(card):
    return [name for name, pattern in ROLES.items() if re.search(pattern, card["effect"])]


def rune_info(cards):
    required = {r: max((c["runes"][r] for c in cards), default=0)
                for r in ("blood", "frost", "unholy")}
    labels = {"blood": "鲜血", "frost": "冰霜", "unholy": "邪恶"}
    return {"required": required, "used_slots": sum(required.values()),
            "free_slots": max(0, 3 - sum(required.values())),
            "summary": " / ".join(f"{labels[r]}{n}" for r, n in required.items() if n) or "卡牌未限定符文",
            "valid": sum(required.values()) <= 3,
            "rule": "共三枚符文，可重复同色；按全套每色最大需求计算，同一需求不会按卡牌张数累加"}


def is_azalina(card):
    return card["dbf_id"] == 126055 or card.get("english_name", "").casefold() == "azalina soulsever"


def deck_size_info(cards, total):
    """The submitted Azalina list is 20 cards, not an incomplete 30-card deck.

    Identity and the current public rules text are both checked; unrelated
    20-card lists and the older Azalina Soulthief do not enable this exception.
    """
    enabler = next((c for c in cards if is_azalina(c) and re.search(r"套牌.{0,6}20张牌", c["effect"])), None)
    if total == 20 and enabler:
        return {"valid": True, "submitted_cards": 20, "starting_deck_cards": 40,
                "opponent_copies": 20, "enabler": enabler["name"], "enabler_dbf_id": enabler["dbf_id"],
                "rule": enabler["effect"],
                "analysis_scope": "20牧自带20张，开局另外复制20张对手卡牌；曲线、费用、粉尘只统计自带部分，对手复制牌未知"}
    return {"valid": total in (30, 40), "submitted_cards": total,
            "rule": "常规构筑30/40张；20张须主牌含当前版本裂魂者阿扎莉娜的特殊构筑效果"}


def decode(code):
    if not isinstance(code, str) or len(code) > 1600 or not CODE.fullmatch(code):
        raise ValueError("卡组代码格式不正确")
    try:
        raw = base64.b64decode(code + "=" * (-len(code) % 4), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("卡组代码不完整") from exc
    pos = 0

    def number():
        nonlocal pos
        value = 0
        for shift in range(0, 35, 7):
            if pos >= len(raw):
                raise ValueError("卡组代码被截断")
            byte = raw[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError("卡组代码数字过长")

    if number() != 0 or number() != 1:
        raise ValueError("不支持的卡组编码版本")
    mode = {1: "wild", 2: "standard"}.get(number())
    if mode is None:
        raise ValueError("目前只支持标准和狂野卡组")
    if number() != 1:
        raise ValueError("卡组必须包含一个英雄")
    hero = number()

    def section(side=False):
        entries = []
        for fixed_count in (1, 2, None):
            size = number()
            if size > 80:
                raise ValueError("卡组条目过多")
            for _ in range(size):
                card = number()
                count = fixed_count if fixed_count else number()
                owner = number() if side else None
                if card <= 0 or not 1 <= count <= 40 or (side and not owner):
                    raise ValueError("卡组卡牌数量异常")
                entries.append((card, count, owner) if side else (card, count))
        if len({(x[0], x[2] if side else None) for x in entries}) != len(entries):
            raise ValueError("卡组含重复编码条目")
        return entries

    cards = section()
    sideboards = []
    if pos < len(raw):
        flag = number()
        if flag not in (0, 1):
            raise ValueError("无法识别备选牌编码")
        if flag:
            sideboards = section(True)
    # Current client exports may append zero-valued optional sections.
    # Do not silently ignore nonzero data from an unsupported future format.
    if any(raw[pos:]):
        raise ValueError("包含尚未支持的卡组扩展编码")
    if sum(n for _, n in cards) > 60 or not cards:
        raise ValueError("卡组总张数异常")
    if any(owner not in dict(cards) for _, _, owner in sideboards):
        raise ValueError("备选牌缺少对应主牌")
    return {"mode": mode, "hero": hero, "cards": cards, "sideboards": sideboards}


def playable_card(card, catalog, mode):
    if card[mode]:
        return card
    # Original prints of a current Core card can appear in valid Standard codes.
    if mode == "standard":
        return next((c for c in catalog.values() if c["standard"] and
                     c["set_code"].upper() == "CORE" and c["name"] == card["name"]), None)
    return None


def analyze(code, catalog, *, expected_mode=""):
    decoded = decode(code)
    mode = decoded["mode"]
    if expected_mode and mode != expected_mode:
        raise ValueError(f"代码是{MODES[mode]}，与要求的{MODES[expected_mode]}不一致")
    missing, unavailable, cards, sideboards = [], [], [], []
    for kind, entries in ((cards, decoded["cards"]), (sideboards, decoded["sideboards"])):
        for entry in entries:
            dbf_id, count = entry[:2]
            card = catalog.get(dbf_id)
            if card is None:
                missing.append(dbf_id)
                continue
            current = playable_card(card, catalog, mode)
            if current is None or not current["collectible"]:
                unavailable.append(card["name"])
            row = {**(current or card), "count": count, "code_dbf_id": dbf_id,
                   "roles": roles(current or card)}
            if kind is sideboards:
                row["owner_dbf_id"] = entry[2]
            kind.append(row)
    cards.sort(key=lambda c: (c["mana"], c["name"]))
    total = sum(n for _, n in decoded["cards"])
    curve = Counter()
    types = Counter()
    functionality = Counter()
    for c in cards:
        curve[str(c["mana"]) if c["mana"] < 7 else "7+"] += c["count"]
        types[c["type"]] += c["count"]
        for role in c["roles"]:
            functionality[role] += c["count"]
    factions = Counter()
    for c in cards:
        for faction in re.split(r"[,;/|]", c["class"]):
            if faction and faction.lower() != "neutral":
                factions[faction] += c["count"]
    faction = factions.most_common(1)[0][0] if factions else "Neutral"
    copies = Counter()
    invalid_copies = []
    for c in cards:
        copies[c["name"]] += c["count"]
        if copies[c["name"]] > (1 if c["rarity"] == "传说" else 2):
            invalid_copies.append(c["name"])
    rune_allocation = rune_info(cards + sideboards)
    runes = rune_allocation["required"]
    size = deck_size_info(cards, total)
    return {
        "code": code, "mode": mode, "mode_name": MODES[mode], "class": faction,
        "class_name": CLASSES.get(faction, faction), "total_cards": total,
        "cards": cards, "sideboards": sideboards, "deck_size": size,
        "mana_curve": {str(i): curve[str(i)] for i in range(7)} | {"7+": curve["7+"]},
        "average_mana": round(sum(c["mana"] * c["count"] for c in cards) / total, 2) if not missing else None,
        "mana_curve_complete": not missing,
        "card_types": dict(types), "effect_roles": dict(functionality), "runes": runes,
        "rune_allocation": rune_allocation if faction.casefold() == "deathknight" else None,
        "dust_main": sum(c["dust"] * c["count"] for c in cards) if not missing else None,
        "dust_sideboard": sum(c["dust"] * c["count"] for c in sideboards) if not missing else None,
        "dust_note": "营地普通品质合成费用估算（含免费牌0尘）；未扣除已有收藏，备选牌单列",
        "validation": {
            "complete": not missing, "unknown_dbf_ids": missing,
            "outside_current_card_pool": unavailable, "over_copy_limit": invalid_copies,
            "runes_valid": sum(runes.values()) <= 3,
            "deck_size_valid": size["valid"],
            "note": "校验卡池、张数与符文；特殊构筑规则及临时禁用仍以游戏客户端为准",
        },
    }


def replacement_candidates(deck, target, catalog, *, budget=None):
    matches = [c for c in deck["cards"] if target.casefold() in
               {c["name"].casefold(), c["english_name"].casefold(), str(c["dbf_id"])}]
    if not matches:
        raise ValueError("这张牌不在主牌中，请给出卡组内的准确卡名")
    old = matches[0]
    if deck["total_cards"] == 20 and is_azalina(old):
        return {"target": old, "candidates": [],
                "limits": "裂魂者阿扎莉娜启用20张构筑和复制对手牌的规则，不能普通一换一；移除后必须重构整套"}
    counts = Counter({c["name"]: c["count"] for c in deck["cards"]})
    counts[old["name"]] -= 1
    names = set(counts)
    odd = "噬月者巴库" in names
    even = "吉恩·格雷迈恩" in names
    highlander = any("没有相同" in c["effect"] or "没有重复" in c["effect"] for c in deck["cards"])
    remaining = [c for c in deck["cards"] if c["name"] != old["name"] or c["count"] > 1]
    remaining += deck["sideboards"]
    if any(c["owner_dbf_id"] == old["code_dbf_id"] for c in deck["sideboards"]):
        return {"target": old, "candidates": [],
                "limits": "此牌携带备选牌；移除它会连带改变备选牌，需先说明要保留的功能，不能直接一换一"}
    result, seen = [], set()
    for candidate in catalog.values():
        c = playable_card(candidate, catalog, deck["mode"])
        if c is None or not c["collectible"] or c["name"] == old["name"] or c["name"] in seen:
            continue
        if is_azalina(c) and deck["total_cards"] != 20:
            continue
        classes = {v.casefold() for v in re.split(r"[,;/|]", c["class"])}
        if not {"neutral", deck["class"].casefold()} & classes:
            continue
        if counts[c["name"]] >= (1 if highlander or c["rarity"] == "传说" else 2):
            continue
        if (odd and c["mana"] % 2 == 0) or (even and c["mana"] % 2 != 0):
            continue
        if budget is not None and c["dust"] > budget:
            continue
        if sum(max([x["runes"][r] for x in remaining] + [c["runes"][r]])
               for r in ("blood", "frost", "unholy")) > 3:
            continue
        if sum(deck["runes"].values()) == 3 and any(c["runes"][r] > deck["runes"][r] for r in deck["runes"]):
            continue
        shared = sorted(set(roles(old)) & set(roles(c)))
        gap = abs(c["mana"] - old["mana"])
        if not shared or gap > 3:
            continue
        score = len(shared) * 4 - gap * 2 + (c["type"] == old["type"]) * 2
        score += bool(c["tribe_or_school"] and c["tribe_or_school"] == old["tribe_or_school"])
        seen.add(c["name"])
        result.append({**c, "shared_roles": shared, "mana_change": c["mana"] - old["mana"],
                       "dust_change": c["dust"] - old["dust"], "match_score": score})
    result.sort(key=lambda c: (-c["match_score"], c["dust"], c["mana"], c["name"]))
    return {"target": old, "candidates": result[:8],
            "basis": "按职业、模式卡池、重复张数、奇偶、宇宙及符文筛选，再比较效果职责与费用",
            "limits": "这是候选比较，不代表替换后胜率；关键组合组件可能没有等价替代，需结合整套打法说明损失"}
