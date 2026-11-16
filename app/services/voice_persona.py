"""音色别名与旧选择命令解析，不加载音色对应的人格提示词。"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.services.band_characters import BAND_CHARACTERS

_ROLE_ALIASES = {
    "dafeiyu": ("大肥鱼", "肥鱼", "鲸鱼娘", "小鲸鱼", "鲸娘", "本鱼"),
    "murasame": ("小丛雨", "丛雨", "穗织幼刀姬"),
    "yoshino": ("朝武芳乃", "芳乃", "巫女姬"),
    "mako": ("常陆茉子", "茉子"),
    "aimisi": ("爱弥斯", "飞行雪绒"),
    "lena": ("蕾娜", "蕾娜·列支敦瑙尔", "蕾娜・列支敦瑙尔", "lena", "rena"),
    "roka": ("马庭芦花", "芦花", "芦花姐"),
    "koharu": ("鞍马小春", "小春"),
}

_ROLE_ALIASES.update({profile_id: (label, *aliases) for profile_id, label, aliases, *_ in BAND_CHARACTERS})


@dataclass(frozen=True)
class Invocation:
    profile_id: str
    prompt: str


_DIRECT_ADDRESS_START = re.compile(
    r"^(?:你|请你|麻烦你|你这个|别装死|出来|过来|来|陪我|和我|跟我|"
    r"聊聊|聊聊天|说说话|听我说|帮我|回答我|告诉我|给我)"
)
_THIRD_PERSON_QUERY_START = re.compile(
    r"^(?:是谁|是什么|哪个角色|介绍|讲讲|说说|查找|查询|搜索|"
    r"她|他|这个角色|这名角色|该角色|原作|作品|设定|背景|资料|信息)"
)


def is_self_introduction_request(prompt: str) -> bool:
    """只把明确询问机器人自身身份的短请求识别为自我介绍。"""
    compact = re.sub(r"[\s，,。.!！?？~～]", "", str(prompt or ""))
    if not compact or len(compact) > 60:
        return False
    if re.search(r"(?:别|不要|不用|无需|不必).*(?:介绍|你是谁|你叫什么)", compact):
        return False
    compact = re.sub(r"^(?:小?丛雨[:：]?|请问|请你|麻烦你|请)+", "", compact)
    return bool(re.fullmatch(
        r"(?:你是谁|你叫什么(?:名字)?|你的名字(?:是什么|叫什么)|"
        r"你是什么身份|介绍(?:一下|下)?你自己|介绍(?:一下|下)?自己|"
        r"(?:做(?:一下|个)?|来(?:一下|个)?)?自我介绍(?:一下|下)?)"
        r"(?:呀|啊|呢|吧|吗)?",
        compact,
    ))


def _canonical_profile_id(profile_id: str) -> str:
    lowered = str(profile_id).casefold()
    return "lena" if lowered == "rena" else lowered


def _normalized_alias(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _persona_aliases(
    profiles: Mapping[str, Mapping[str, object]],
) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    for profile_id, profile in profiles.items():
        canonical = _canonical_profile_id(profile_id)
        names = (
            *_ROLE_ALIASES.get(canonical, ()),
            profile_id,
            str(profile.get("label") or ""),
        )
        for name in names:
            normalized = _normalized_alias(name)
            if normalized:
                aliases.setdefault(normalized, set()).add(profile_id)
    return aliases


def resolve_voice_persona_alias(
    selection: str, profiles: Mapping[str, Mapping[str, object]]
) -> str | None:
    """使用和召唤命令相同的别名，解析角色全名或简称。"""
    requested = _normalized_alias(selection)
    if not requested:
        return None
    aliases = _persona_aliases(profiles)
    # 如果旧别名同时被两个角色使用（例如 lena 和 rena），
    # 优先精确匹配已配置的 profile ID。
    for profile_id in profiles:
        if _normalized_alias(profile_id) == requested:
            return profile_id
    matches = aliases.get(requested, set())
    return next(iter(matches)) if len(matches) == 1 else None


def character_invocation(
    text: str, profiles: Mapping[str, Mapping[str, object]]
) -> Invocation | None:
    """识别明确的角色召唤，不匹配第三人称叙述。

    调用方只提供已授权且可用的角色。返回选中的 ID 和对话内容；内容为空时表示用户只要求简单问候。
    """
    candidate = str(text or "").strip()
    aliases = _persona_aliases(profiles)
    mentioned = set()
    for alias, ids in aliases.items():
        pattern = re.escape(alias)
        if alias.isascii():
            pattern = r"(?<![a-z0-9_])" + pattern + r"(?![a-z0-9_])"
        if re.search(pattern, candidate, re.IGNORECASE):
            mentioned.update(ids)
    if len(mentioned) != 1:
        return None
    profile_id = next(iter(mentioned))
    names = sorted(
        (a for a, ids in aliases.items() if profile_id in ids),
        key=len,
        reverse=True,
    )
    name_pattern = "(?:" + "|".join(map(re.escape, names)) + ")"
    patterns = (
        rf"(?:请)?{name_pattern}[!！。~～?？]*",
        rf"(?:请)?(?:叫|让|请|麻烦)?\s*{name_pattern}\s*(?:你\s*)?(?:出来|过来|来)(?:一下|下)?(?:吧|呀|啊|嘛)?[!！。~～]*",
        rf"(?:请)?(?:切换(?:到|成|为)?|换成|换到)\s*(?:角色\s*)?{name_pattern}(?:的人格|人格)?[!！。~～]*",
    )
    if any(re.fullmatch(pattern, candidate, re.IGNORECASE) for pattern in patterns):
        return Invocation(profile_id, "")

    # 逗号或冒号表示明确称呼；没有标点时，
    # 只接受常见的第二人称请求，避免误处理叙述中提到的名字。
    match = re.fullmatch(
        rf"{name_pattern}\s*(?P<separator>[,，:：]\s*)?(?P<prompt>.+)",
        candidate,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        prompt = match["prompt"].strip()
        separator = match["separator"]
        if _THIRD_PERSON_QUERY_START.match(prompt):
            return None
        if separator or _DIRECT_ADDRESS_START.match(prompt):
            return Invocation(profile_id, prompt)
    return None


def voice_persona_romance_prompt(profile_label: str, conversation_turn_count: int = 0) -> str:
    """为丛雨以外的角色提供通用恋爱模式节奏。"""
    turns = max(0, min(int(conversation_turn_count), 12))
    if turns <= 2:
        pace = "刚开始熟悉：像自然、温柔的聊天对象，最多只有一点含蓄，不主动撒娇或强行暧昧。"
    elif turns <= 7:
        pace = "逐渐熟络：可以偶尔流露关心或轻微害羞，但仍以自然聊天为主，每条最多一处。"
    else:
        pace = "已经熟悉：只有话题和对方态度都合适时，才偶尔亲昵或害羞；不要变成固定口癖。"
    return (
        f"【恋爱模式语气】恋爱模式已开启，当前角色是“{profile_label}”。保持该角色原有身份、用词和性格，"
        "亲近感随对话自然发展，不要把现实用户擅自当作原作中的某位角色，不制造排他、依赖、愧疚或现实承诺。"
        "先清楚回答当前问题；求助、事实、技术、悲伤或争执话题仍认真可靠。"
        + pace
    )
