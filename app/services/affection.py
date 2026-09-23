import json
import re
from dataclasses import dataclass

import httpx

from app.llm.providers import LLMError

AFFECTION_MIN = 0
AFFECTION_MAX = 100
AFFECTION_INITIAL = 40
MEMORY_UNLOCK_SCORE = 55

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
    "畜生",
    "出生",
    "狗东西",
    "死机器人",
    "妈的",
    "操你",
    "草你",
    "操你妈",
    "草你妈",
    "cnm",
    "nmsl",
    "司马",
    "妈死",
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
    "抱抱",
    "陪我聊聊",
)


@dataclass(frozen=True)
class AffectionAssessment:
    delta: int
    reason: str
    source: str = "rule"


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
        question_rule = "偶尔在自然场景反问一个很短的问题，不要每次都问。"
    elif score < 60:
        length_rule = "普通闲聊通常2到3句。"
        question_rule = "可以偶尔问对方今天在做什么、吃了什么、最近在玩什么等轻松日常。"
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
        f"只有好感度达到 {MEMORY_UNLOCK_SCORE} 才能新增“记住”类持久记忆。"
    )


def affection_status_text(score: int) -> str:
    stage, _ = affection_stage(score)
    unlock = (
        "已解锁记住功能"
        if score >= MEMORY_UNLOCK_SCORE
        else f"记住功能将在 {MEMORY_UNLOCK_SCORE} 解锁"
    )
    return f"小丛雨好感度：{clamp_affection(score)}/100｜{stage}｜{unlock}"


def rule_based_affection(text: str, previous_bot_reply: str = "") -> AffectionAssessment:
    compact = re.sub(r"\s+", "", str(text or "")).casefold()
    if not compact:
        return AffectionAssessment(0, "没有可结算内容")

    if any(token in compact for token in _SEVERE_HOSTILITY):
        return AffectionAssessment(-10, "明显恶意辱骂")
    if any(token in compact for token in _MODERATE_HOSTILITY):
        return AffectionAssessment(-5, "不友善或攻击性表达")
    if any(token in compact for token in _MILD_HOSTILITY):
        return AffectionAssessment(-2, "明显不满或轻度恶言")

    if any(token in compact for token in _STRONG_POSITIVE):
        return AffectionAssessment(5, "明确称赞或非常满意")
    if any(token in compact for token in _POSITIVE):
        return AffectionAssessment(3, "友好回应或明确肯定")
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


async def assess_affection(
    text: str,
    previous_bot_reply: str,
    llm=None,
) -> AffectionAssessment:
    direct = rule_based_affection(text, previous_bot_reply)
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
