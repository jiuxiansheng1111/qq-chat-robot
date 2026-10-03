"""Character dialogue personas paired with configured voice profiles."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

_PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts" / "voice_personas"

# Voice profile IDs with generated dialogue personalities.  The additional
# Senren*Banka roles are prepared here but stay out of the selectable voice
# menu until their own verified voice profiles are configured.
_PERSONA_FILES = {
    "yoshino": "yoshino.txt",
    "mako": "mako.txt",
    "aimisi": "aimisi.txt",
    "lena": "lena.txt",
    "koharu": "koharu.txt",
    "roka": "roka.txt",
}

_ROLE_ALIASES = {
    "murasame": ("小丛雨", "丛雨", "穗织幼刀姬"),
    "yoshino": ("朝武芳乃", "芳乃", "巫女姬"),
    "mako": ("常陆茉子", "茉子"),
    "aimisi": ("爱弥斯", "飞行雪绒"),
    "lena": ("蕾娜", "蕾娜·列支敦瑙尔", "蕾娜・列支敦瑙尔", "lena", "rena"),
    "roka": ("马庭芦花", "芦花", "芦花姐"),
    "koharu": ("鞍马小春", "小春"),
}


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
        if not has_voice_persona(canonical):
            continue
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
    """Resolve a role's full or short name using the same aliases as summons."""
    requested = _normalized_alias(selection)
    if not requested:
        return None
    aliases = _persona_aliases(profiles)
    # Prefer an exact configured profile ID when a legacy alias is shared by
    # both IDs, such as ``rena`` when both lena and rena are configured.
    for profile_id in profiles:
        if _normalized_alias(profile_id) == requested and has_voice_persona(
            _canonical_profile_id(profile_id)
        ):
            return profile_id
    matches = aliases.get(requested, set())
    return next(iter(matches)) if len(matches) == 1 else None


def character_invocation(
    text: str, profiles: Mapping[str, Mapping[str, object]]
) -> Invocation | None:
    """Recognize an explicit summons, never a substring in third-person prose.

    The caller supplies only authorized, available profiles. Return the selected
    ID and dialogue payload; an empty payload is a simple greeting request.
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

    # A comma/colon is a clear vocative marker. Without punctuation, accept
    # only familiar second-person requests so mentions in prose stay inert.
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


def has_voice_persona(profile_id: str) -> bool:
    profile_id = "lena" if profile_id == "rena" else profile_id
    if profile_id == "murasame":
        return True
    filename = _PERSONA_FILES.get(profile_id)
    return bool(
        filename
        and (_PROMPT_ROOT / "common.txt").is_file()
        and (_PROMPT_ROOT / filename).is_file()
    )


def voice_persona_prompt(settings: Settings, profile_id: str) -> str | None:
    """Return a complete system persona, or None for an unconfigured role."""
    profile_id = "lena" if profile_id == "rena" else profile_id
    if profile_id == "murasame":
        return settings.persona_prompt()
    filename = _PERSONA_FILES.get(profile_id)
    if filename is None:
        return None

    common_path = _PROMPT_ROOT / "common.txt"
    role_path = _PROMPT_ROOT / filename
    try:
        common = common_path.read_text(encoding="utf-8").strip()
        role = role_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not common or not role:
        return None
    return f"{common}\n\n{role}"


def voice_persona_romance_prompt(profile_label: str, conversation_turn_count: int = 0) -> str:
    """Role-neutral romance-mode pacing for non-Murasame character voices."""
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
