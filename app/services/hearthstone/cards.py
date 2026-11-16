"""Card lookup grammar and matching. Keep individual printings selectable."""
from __future__ import annotations

import re
import unicodedata
from difflib import get_close_matches

_REQUEST = re.compile(
    r"^/?(?:(?:请|麻烦)(?:你)?[，,\s]*)?(?:(?:帮我|给我|替我|帮忙)(?:再)?\s*)?"
    r"(?:炉石(?:传说)?\s*)?"
    r"(?:(?:查询|查一下|查下|查)\s*(?:一下\s*)?(?:炉石(?:传说)?\s*)?卡[牌片]"
    r"|卡[牌片]查询|查卡(?!组|号|余额|路里|顿|确认|取消))"
    r"\s*[:：]?\s*(?P<query>[^\r\n]*)$"
)
_META = re.compile(r"^(?:的?(?:帮助|用法|说明|菜单|功能|命令|指令)|怎么用|如何用|没(?:有)?接入|不能用|为什么|好用吗|能用吗)")
_MODE = re.compile(r"^(标准|狂野)(?:[\s:：]+|$)")
# Colloquial names that cannot be recovered by matching the printed name.
# Aliases identify names, not printings. Each matching edition remains an option.
_ALIASES = {
    "姐夫": ("了不起的杰弗里斯", "梦想策划师杰弗里斯"),
    "老姐夫": ("了不起的杰弗里斯",), "新姐夫": ("梦想策划师杰弗里斯",),
    "大螺丝": ("炎魔之王拉格纳罗斯",), "光螺丝": ("光耀之主拉格纳罗斯",),
    "螺丝": ("炎魔之王拉格纳罗斯", "光耀之主拉格纳罗斯", "拉格纳罗斯，绝世烈火"),
    "哀绿": ("巫师学徒",), "塞布": ("洛欧塞布",),
    "大范": ("艾德温·范克里夫", "艾德温，迪菲亚首脑"), "小范": ("梵妮莎·范克里夫",),
    "血法": ("血法师萨尔诺斯",), "吼爹": ("格罗玛什·地狱咆哮",), "老牛": ("凯恩·血蹄",),
    "佛丁": ("提里奥·弗丁", "泰兰·弗丁"), "老佛爷": ("提里奥·弗丁",),
    "女王": ("希尔瓦娜斯·风行者", "被告希尔瓦娜斯", "游侠将军希尔瓦娜斯"),
    "红龙": ("阿莱克丝塔萨", "红龙女王阿莱克丝塔萨", "生命的缚誓者阿莱克丝塔萨", "阿莱克丝塔萨，生命守护者"),
    "蓝龙": ("玛里苟斯", "织法巨龙玛里苟斯", "织法者玛里苟斯", "碧蓝之王玛里苟斯"),
}
_SEPARATORS = str.maketrans("", "", "·•‧・∙-'")
_WORDS = re.compile(r"[\s·•‧・∙,，-]+")


def normalized_name(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value))).casefold().replace("’", "'")


def card_name_key(value):
    return normalized_name(value).translate(_SEPARATORS)


def matches_card_answer(card, answer):
    key = card_name_key(str(answer).strip(" 《》「」"))
    return bool(key and (key in {card_name_key(card["name"]), card_name_key(card.get("english_name", ""))}
                         or card["name"] in _ALIASES.get(key, ())))


def _initials(name):
    words = _WORDS.split(name.strip())
    return "".join(w[:1] for w in words).casefold() if len(words) > 1 else ""


def parse_card_lookup(text):
    match = _REQUEST.fullmatch(str(text or "").strip())
    if not match:
        return None
    query = match["query"].strip()
    if _META.match(query):
        return None
    mode = ""
    prefix = _MODE.match(query)
    if prefix:
        mode = "standard" if prefix[1] == "标准" else "wild"
        query = query[prefix.end():].strip()
    for left, right in (("《", "》"), ("“", "”"), ("「", "」"), ('"', '"')):
        if query.startswith(left) and query.endswith(right):
            query = query[1:-1].strip()
            break
    return mode, query


def find_cards(catalog, query, mode):
    key = card_name_key(query)
    if not key:
        return [], False
    effect = query.strip().casefold()
    aliases = _ALIASES.get(key, ())
    exact_id = key.isdecimal()
    ranked = []
    names = {}
    for card in catalog:
        if mode and not card[mode]:
            continue
        keys = (card_name_key(card["name"]), card_name_key(card["english_name"]))
        if key == str(card["dbf_id"]) or key in keys:
            rank = 0
        elif exact_id:
            continue
        elif card["name"] in aliases:
            rank = 1
        elif any(key in name for name in keys) or (
            len(key) >= 2 and key in (_initials(card["name"]), _initials(card["english_name"]))
        ):
            rank = 2
        elif effect in card["effect"].casefold():
            rank = 3
        else:
            for name in keys:
                if name:
                    names.setdefault(name, []).append(card)
            continue
        ranked.append((rank, card))
    # When a player named a card, other cards merely mentioning it in their
    # rules are not alternative printings. Effect-only queries still work.
    if any(rank <= 1 for rank, _ in ranked):
        ranked = [(rank, card) for rank, card in ranked if rank < 3]
    similar = not ranked and not exact_id and len(key) >= 2
    if similar:
        close = get_close_matches(key, names, n=8, cutoff=0.65 if len(key) >= 3 else 0.75)
        seen = set()
        for rank, name in enumerate(close):
            for card in names[name]:
                if card["dbf_id"] not in seen:
                    seen.add(card["dbf_id"])
                    ranked.append((rank, card))
    alias_order = {name: index for index, name in enumerate(aliases)}
    ranked.sort(key=lambda row: (row[0], alias_order.get(row[1]["name"], 0), not row[1]["collectible"], not row[1]["standard"],
                                 -int(row[1].get("updated") or 0), row[1]["dbf_id"]))
    return [card for _, card in ranked], bool(similar and ranked)
