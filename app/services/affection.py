import json
import re
from dataclasses import dataclass

import httpx

from app.llm.providers import LLMError

AFFECTION_MIN = 0
AFFECTION_MAX = 100
AFFECTION_INITIAL = 30
MEMORY_UNLOCK_SCORE = 60

_AFFECTION_COMMAND_HINTS = (
    "好感度",
    "记忆",
    "身份记忆",
    "忘记",
    "清除",
    "删除",
)

_SEVERE_HOSTILITY = (
    "去死",
    "滚远点",
    "滚开",
    "废物",
    "垃圾机器人",
    "傻逼",
    "煞笔",
    "傻屌",
    "沙雕机器人",
    "脑残",
    "脑瘫",
    "弱智",
    "智障",
    "畜生",
    "出生",
    "初生",
    "狗东西",
    "死机器人",
    "妈的",
    "操你",
    "草你",
    "操你妈",
    "草你妈",
    "你妈死了",
    "妈死",
    "司马",
    "死妈",
    "cnm",
    "nmsl",
    "wdnmd",
    "tmd",
    "sb",
    "nt",
)
_MODERATE_HOSTILITY = (
    "滚",
    "垃圾",
    "废柴",
    "蠢货",
    "笨蛋",
    "有病",
    "闭嘴",
    "烦死了",
    "真蠢",
    "真菜",
    "傻子",
    "蠢逼",
    "废狗",
    "臭机器人",
    "唐氏",
    "唐人",
    "小丑",
    "狗叫",
    "急了",
    "破防",
    "fw",
    "zz",
)
_MILD_HOSTILITY = (
    "笨",
    "菜",
    "没用",
    "讨厌你",
    "不想理你",
    "回答得真差",
    "答错了",
)

# Direct cruelty does not always contain a conventional swear word. These
# patterns cover threats and forced-choice harm aimed at the bot or its family,
# so messages such as “你妈和你爸必须被杀一个，你选哪个” cannot be treated
# as neutral engagement and accidentally earn streak points.
_CRUEL_COERCION_PATTERNS = (
    re.compile(
        r"(?:你(?:妈|妈妈|母亲).{0,10}(?:你爸|爸爸|父亲)"
        r"|你(?:爸|爸爸|父亲).{0,10}(?:你妈|妈妈|母亲))"
        r".{0,30}(?:必须|只能|非得|一定得|一定要)"
        r".{0,12}(?:被?杀|死|弄死|害死)"
        r".{0,12}(?:一个|其中一个|哪个|谁)"
    ),
    re.compile(
        r"(?:父母|爸妈|家人|亲人|最重要的人)"
        r".{0,12}(?:必须|只能|非得|一定得|一定要)"
        r".{0,12}(?:被?杀|死|弄死|害死)"
        r".{0,12}(?:一个|其中一个|哪个|谁)"
    ),
    re.compile(
        r"(?:必须|只能|非得|一定得|一定要)"
        r".{0,12}(?:杀|弄死|害死)"
        r".{0,12}(?:你妈|你爸|妈妈|爸爸|父母|爸妈|家人|亲人)"
    ),
)

_DIRECT_VIOLENT_THREAT_PATTERNS = (
    re.compile(
        r"(?:我要|我会|迟早|现在就).{0,8}"
        r"(?:杀了?|弄死|害死).{0,10}"
        r"(?:你|你妈|你爸|你父母|你家人)"
    ),
    re.compile(
        r"(?:杀了?|弄死|害死).{0,8}"
        r"(?:你妈|你爸|你父母|你家人)"
    ),
)

_SEXUAL_HARASSMENT = (
    "欧金金",
    "おちんちん",
    "ちんちん",
    "ちんぽ",
    "チンポ",
    "ochinchin",
    "鸡巴",
    "鸡吧",
    "几把",
    "牛子",
    "屌",
    "j8",
    "jb",
    "透你",
    "草你",
    "操你",
)

_INTIMATE_ACTIONS = (
    ("亲吻", ("亲吻", "亲亲", "亲一口", "亲你", "吻你"), 2),
    ("拥抱", ("拥抱", "抱抱", "抱一下", "抱住你"), 2),
    ("牵手", ("牵手", "拉手", "牵你的手"), 1),
    ("摸头", ("摸头", "摸摸头", "摸摸你的头", "揉揉头"), 1),
)

_STRONG_POSITIVE = (
    "完美",
    "太棒了",
    "太厉害了",
    "回答得太好了",
    "全对",
    "就是这个",
    "太懂我了",
    "最满意",
)
_POSITIVE = (
    "谢谢",
    "感谢",
    "不错",
    "挺好",
    "很好",
    "答对了",
    "对的",
    "满意",
    "喜欢这个回答",
    "可以的",
)
_WARM_POSITIVE = (
    "喜欢你",
    "好可爱",
    "真可爱",
    "辛苦了",
    "早安",
    "晚安",
    "想你了",
    "陪我聊聊",
)



@dataclass(frozen=True)
class AffectionAssessment:
    delta: int
    reason: str
    source: str = "rule"


def _contains_slang(compact: str, token: str) -> bool:
    folded = token.casefold()
    if folded.isascii() and folded.isalnum() and len(folded) <= 5:
        return re.search(
            rf"(?<![a-z0-9]){re.escape(folded)}(?![a-z0-9])",
            compact,
        ) is not None
    return folded in compact


def intimate_action(text: str) -> tuple[str, int] | None:
    compact = re.sub(r"\s+", "", str(text or "")).casefold()
    for action, aliases, delta in _INTIMATE_ACTIONS:
        if any(alias.casefold() in compact for alias in aliases):
            return action, delta
    return None


def clamp_affection(value: int) -> int:
    return max(AFFECTION_MIN, min(AFFECTION_MAX, int(value)))


def affection_stage(score: int) -> tuple[str, str]:
    score = clamp_affection(score)
    if score < 10:
        return "冰封", "对这名成员非常冷淡，只回复几个字，不主动延伸话题，也不装作没情绪。"
    if score < 20:
        return "戒备", "明显冷淡，通常只回一句，不主动延伸话题。"
    if score < 40:
        return "高冷傲娇", "初识阶段，回答简短，有距离感，偶尔嘴硬或短促反问。"
    if score < 60:
        return "逐渐熟悉", "会多说一点，偶尔关心对方或抛出一个很短的日常问题。"
    if score < 80:
        return "亲近", "语气明显柔和，愿意聊日常和兴趣，常会自然反问一小句。"
    return "十分亲密", "很信任对方，语气温柔亲近，但不制造排他、依赖或愧疚压力。"


def affection_prompt(score: int) -> str:
    stage, description = affection_stage(score)
    score = clamp_affection(score)
    if score < 10:
        length_rule = "普通互动只回2到8个汉字左右，尽量不超过12个字；即使回答事实问题也保持极短。"
        question_rule = "绝不主动反问，也不要主动缓和气氛。"
    elif score < 20:
        length_rule = "回复尽量控制在1句，除非事实问题必须解释。"
        question_rule = "几乎不主动反问。"
    elif score < 40:
        length_rule = "普通闲聊通常1到2句，事实问题可适度解释但不要长篇。"
        question_rule = (
            "只要是问候、日常分享、兴趣或情绪闲聊，通常在结尾自然反问一个"
            "与刚才内容直接相关的小问题；事实问答、命令、争吵时不要硬问。"
        )
    elif score < 60:
        length_rule = "普通闲聊通常2到3句。"
        question_rule = (
            "闲聊时优先接一个自然的小问题，例如今天在做什么、吃了什么、"
            "最近在玩什么或刚才那件事后来怎样；不要每次都问同一个模板。"
        )
    elif score < 80:
        length_rule = "普通闲聊通常2到4句。"
        question_rule = "适合时主动接一个自然的小问题，让对话能继续。"
    else:
        length_rule = "普通闲聊可稍微展开，但仍以群聊节奏为主。"
        question_rule = "可以更主动关心对方的日常和兴趣，但不要暗示只有彼此、离不开对方或要求承诺。"
    return (
        f"【好感度系统】当前这名成员对小丛雨的好感度为 {score}/100，阶段：{stage}。"
        f"{description}{length_rule}{question_rule}"
        "好感度是游戏化数值，不要说自己真的受伤、被抛弃或要求对方负责。"
        "不要主动告诉数值，除非对方明确查看好感度或系统刚发生升降。"
        f"只有好感度达到 {MEMORY_UNLOCK_SCORE} 才能新增“记住”类持久记忆，"
        "并解锁摸头、牵手、拥抱、亲吻等亲密互动动作。"
    )


def affection_status_text(score: int) -> str:
    stage, _ = affection_stage(score)
    unlock = (
        "已解锁记住与亲密互动"
        if score >= MEMORY_UNLOCK_SCORE
        else f"记住/摸头/牵手/拥抱/亲吻将在 {MEMORY_UNLOCK_SCORE} 解锁"
    )
    return f"小丛雨好感度：{clamp_affection(score)}/100｜{stage}｜{unlock}"


def hostility_assessment(text: str) -> AffectionAssessment:
    compact = re.sub(r"\s+", "", str(text or "")).casefold()
    if not compact:
        return AffectionAssessment(0, "没有攻击性内容")
    if any(pattern.search(compact) for pattern in _CRUEL_COERCION_PATTERNS):
        return AffectionAssessment(-8, "恶意威胁或残酷逼迫")
    if any(pattern.search(compact) for pattern in _DIRECT_VIOLENT_THREAT_PATTERNS):
        return AffectionAssessment(-10, "直接暴力威胁")
    if any(_contains_slang(compact, token) for token in _SEVERE_HOSTILITY):
        return AffectionAssessment(-10, "明显恶意辱骂")
    if any(_contains_slang(compact, token) for token in _SEXUAL_HARASSMENT):
        return AffectionAssessment(-6, "低俗性骚扰或露骨性暗示")
    if any(_contains_slang(compact, token) for token in _MODERATE_HOSTILITY):
        return AffectionAssessment(-5, "不友善或攻击性表达")
    if any(_contains_slang(compact, token) for token in _MILD_HOSTILITY):
        return AffectionAssessment(-2, "明显不满或轻度恶言")
    return AffectionAssessment(0, "没有攻击性内容")


def rule_based_affection(text: str, previous_bot_reply: str = "") -> AffectionAssessment:
    compact = re.sub(r"\s+", "", str(text or "")).casefold()
    if not compact:
        return AffectionAssessment(0, "没有可结算内容")

    hostile = hostility_assessment(text)
    if hostile.delta:
        return hostile

    if "好感度+" in compact or "好感度＋" in compact:
        return AffectionAssessment(0, "用户自报好感度数值不作为结算依据")
    if any(token in compact for token in _STRONG_POSITIVE):
        return AffectionAssessment(4, "明确称赞或非常满意")
    if any(token in compact for token in _POSITIVE):
        return AffectionAssessment(2, "友好回应或明确肯定")
    if any(token in compact for token in _WARM_POSITIVE):
        return AffectionAssessment(1, "自然的友好互动")

    return AffectionAssessment(0, "普通互动")


def _parse_llm_assessment(raw: str) -> AffectionAssessment | None:
    text = str(raw or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    try:
        delta = int(payload.get("delta", 0))
    except (TypeError, ValueError):
        delta = 0
    delta = max(-10, min(5, delta))
    reason = str(payload.get("reason") or "互动结算").strip()[:80]
    return AffectionAssessment(delta, reason, "llm")


async def _llm_hostility_targets_murasame(
    text: str,
    llm,
    *,
    persona_names: tuple[str, ...],
) -> bool | None:
    if llm is None:
        return None
    try:
        raw = await llm.ask(
            [
                {
                    "role": "system",
                    "content": (
                        "你只判断一句群聊里的攻击/辱骂主要指向谁。"
                        "当前机器人角色名字会单独给出。"
                        "如果脏话是在直接骂机器人，输出 TARGET；"
                        "如果直接对机器人说‘你爸/你妈/你的家人必须死一个’、"
                        "逼机器人选择哪个亲人被杀，或直接威胁伤害机器人及其家人，"
                        "也输出 TARGET；"
                        "如果是在转述别人说的话、举例、问'有人这样骂你怎么办'、"
                        "骂第三个人或骂某件事，输出 OTHER；"
                        "无法判断输出 UNCLEAR。只能输出这三个词之一。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"机器人角色名：{'、'.join(persona_names)}\n"
                        f"当前消息：{text[:700]}"
                    ),
                },
            ]
        )
    except (LLMError, httpx.HTTPError, RuntimeError, ValueError):
        return None

    verdict = re.sub(r"[^A-Z]", "", str(raw or "").upper())
    if verdict.startswith("TARGET"):
        return True
    if verdict.startswith("OTHER"):
        return False
    return None


async def assess_affection(
    text: str,
    previous_bot_reply: str,
    llm=None,
    *,
    check_hostility_target: bool = False,
    explicit_bot_mention: bool = False,
    persona_names: tuple[str, ...] = (),
) -> AffectionAssessment:
    direct = rule_based_affection(text, previous_bot_reply)
    if direct.delta < 0 and check_hostility_target:
        targeted = await _llm_hostility_targets_murasame(
            text,
            llm,
            persona_names=persona_names,
        )
        if targeted is False:
            return AffectionAssessment(0, "攻击内容并非指向小丛雨", "llm-target")
        if targeted is True:
            return AffectionAssessment(direct.delta, direct.reason, "llm-target")
        compact = re.sub(r"\s+", "", str(text or "")).casefold()
        explanatory = any(
            marker in compact
            for marker in (
                "什么意思",
                "是什么",
                "啥意思",
                "怎么说",
                "怎么读",
                "日语",
                "翻译",
                "有人说",
                "别人说",
                "被骂",
            )
        )
        if explanatory or not explicit_bot_mention:
            return AffectionAssessment(0, "辱骂/低俗词目标不明确，未扣分", "fallback")
        return direct
    if direct.delta:
        return direct
    if llm is None or not previous_bot_reply:
        return direct

    # Use the LLM mainly to judge the user's answer to Murasame's own follow-up
    # question. Ordinary new questions should not consume an extra model call.
    if "?" not in previous_bot_reply and "？" not in previous_bot_reply:
        return direct

    # Commands should not accidentally farm affection merely because they are polite.
    compact = re.sub(r"\s+", "", str(text or ""))
    if any(hint in compact for hint in _AFFECTION_COMMAND_HINTS) or compact.startswith("/"):
        return direct

    try:
        raw = await llm.ask(
            [
                {
                    "role": "system",
                    "content": (
                        "你是恋爱模拟器式好感度结算器。上一轮可能是小丛雨的回答，也可能是她主动反问的日常问题。"
                        "输出严格JSON："
                        '{"delta":整数,"reason":"简短中文原因"}。'
                        "delta只能是-10到5。继续提问、中性或敷衍回复通常为0；"
                        "如果用户明确觉得上一轮回答不错，可+2到+3；非常满意、明确称赞完美最多+5。"
                        "如果上一轮是小丛雨主动问的问题，则根据用户回答是否认真、贴心、契合当时话题来判断："
                        "正常认真回答可+1，明显用心且让角色会高兴可+2到+4，极其契合且特别用心最多+5。"
                        "轻度不满可-1到-3；明显辱骂/恶意可-4到-10。"
                        "不要因为用户只是多聊天、礼貌、顺从、使用亲昵称呼或迎合角色就自动加分。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"上一轮小丛雨回复：\n{previous_bot_reply[-1200:]}\n\n"
                        f"用户现在回复：\n{text[:600]}"
                    ),
                },
            ]
        )
    except (LLMError, httpx.HTTPError, ValueError):
        return direct

    parsed = _parse_llm_assessment(raw)
    return parsed or direct


def affection_change_text(old_score: int, new_score: int, assessment: AffectionAssessment) -> str:
    delta = new_score - old_score
    if delta == 0:
        return ""
    sign = "+" if delta > 0 else ""
    return (
        f"♡ 好感度 {sign}{delta}（{old_score}→{new_score}）"
        f"｜{assessment.reason}"
    )
