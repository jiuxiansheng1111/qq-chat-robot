"""对外角色身份与明确技术配置问句分开处理，不创建回复或扩大唤醒范围。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from app.astrbot_reply_guard import (
    GroupReplyGuard,
    _component_type,
    _is_command,
    _prompt,
    _raw,
)


def persona_expression_settings(persona_path: Path | None = None) -> dict[str, str]:
    """人格、回复纠正与Companion共用主文件，不再分别手写三份语气。"""
    path = persona_path or Path(__file__).resolve().parents[1] / "prompts/persona.txt"
    source = path.read_text(encoding="utf-8-sig")
    sections = {
        match.group(1): match.group(0).strip()
        for match in re.finditer(r"^【([^\n】]+)】[^\n]*\n.*?(?=^【|\Z)", source, re.MULTILINE | re.DOTALL)
    }
    required = ("角色身份与技术配置", "说话性格", "回复风格")
    missing = [name for name in required if name not in sections]
    if missing:
        raise ValueError("persona.txt缺少段落：" + "、".join(missing))
    style = sections["说话性格"]
    label = re.search(r"^- 性格标签：[ \t]*(.+)$", style, re.MULTILINE)
    if label is None:
        raise ValueError("persona.txt的【说话性格】中缺少‘- 性格标签：’")
    return {
        "default_style": label.group(1).strip().rstrip("。"),
        "persona_conversation_voice_prompt": sections["角色身份与技术配置"] + "\n\n" + style,
        "reply_style_prompt": sections["回复风格"],
    }


# 服务启动时加载；修改主文件后同步并重启即可同时更新所有入口。
IDENTITY_STYLE_PROMPT = persona_expression_settings()["persona_conversation_voice_prompt"]
COMPANION_VOICE_PROMPT = IDENTITY_STYLE_PROMPT
CHARACTER_INTRODUCTION = "我是ChatGPT的龙女仆。"
_TECHNICAL = re.compile(
    r"底层|实际(?:上)?(?:用|使用|跑|运行|的)?(?:的|什么|哪个|啥)?模型|真实模型|完整(?:模型)?型号|"
    r"具体(?:模型)?型号|api|供应商|厂商|哪(?:家|个)(?:公司|团队)|由谁(?:开发|训练)|谁(?:开发|训练)的|"
    r"部署|技术配置|后台配置|模型(?:配置|参数|版本号)|别(?:用|说|报)角色|不要(?:用|说|报)角色",
    re.IGNORECASE,
)
_IDENTITY = re.compile(
    r"(?:你(?:到底|究竟)?(?:是|叫)(?:谁|什么|啥|什么名字|啥名字)|你叫什么名字|你是什么身份|"
    r"(?:你(?:是|用的|用的是)?)?(?:什么|啥|哪个)(?:模型|机器人|ai)|"
    r"(?:请)?(?:介绍(?:一下|下)?你自己|自我介绍(?:一下|下)?)|"
    r"你(?:是|是不是|不是)(?:豆包|chatgpt|小爱|小度|文心一言|通义千问)(?:在)?"
    r"(?:扮演|假扮|角色扮演)?(?:的)?|"
    r"你(?:是)?谁(?:做|写|制作)的|谁(?:做|写|制作)的你|"
    r"你(?:是|是不是)(?:真人|人类|现实中的人|人|ai|人工智能))"
    r"(?:吗|么|嘛|呢|呀|啊|吧|哇|哦|啦)?",
    re.IGNORECASE,
)
_NEGATED_OR_DISCUSSION = re.compile(r"不要|不用|别|无需|什么意思|怎么翻译|如何翻译|这句话|这句台词")


def identity_question_kind(text: str) -> Literal["character", "technical"] | None:
    """只识别当前明确询问机器人自身的短问句，讨论/引用/其他人的身份不接管。"""
    value = str(text or "").strip()
    if _TECHNICAL.search(value) and re.search(
        r"你|底层|实际.*模型|真实模型|完整.*型号|具体.*型号|后台配置|技术配置|"
        r"^(?:api供应商|供应商|厂商|模型版本号|模型配置|模型参数)", value, re.IGNORECASE,
    ):
        return "technical"
    if len(value) > 100 or _NEGATED_OR_DISCUSSION.search(value) or re.search(r"[`\"“”‘’「」『』]", value):
        return None
    value = re.sub(r"^(?:小丛雨|丛雨)[，,:：\s]*", "", value)
    value = re.sub(r"[?？!！。～~\s]+$", "", value)
    return "character" if _IDENTITY.fullmatch(value) else None


def identity_request_prompt(text: str, model_name: str | None = None) -> str:
    """可在请求末端补充角色约束；仅明确技术追问携带当前配置确认的型号。"""
    result = IDENTITY_STYLE_PROMPT
    if identity_question_kind(text) == "technical":
        if model_name and isinstance(model_name, str):
            result += "\n当前运行配置确认的模型标识（配置数据）：" + json.dumps(model_name[:120], ensure_ascii=False)
            result += "。只按这个标识回答技术问题，不凭模型自述另猜厂商、版本或开发者。"
        else:
            result += "\n本轮未提供已经核实的运行型号，不能凭自我印象编造技术配置或开发者。"
    return result


def _is_natural_truthful_character_intro(answer: str, question: str) -> bool:
    """保留已经自然、准确说明当前身份的回答；不接受旧角色或不实身份说法。"""
    value = str(answer or "").strip()
    if not re.search(r"chat\s*gpt", value, re.IGNORECASE) or "龙女仆" not in value:
        return False
    if re.search(r"小丛雨|丛雨|豆包|智谱|文心一言|通义千问|小爱|小度", value, re.IGNORECASE):
        return False
    affirms_human = re.search(r"(?:是|就是|确实是|当然是|还是|也是)(?:一个|一名|个)?(?:现实中的)?(?:真人|人类|现实中的人)", value)
    denies_human = re.search(r"(?:不是|并非|并不是|不算|非)\s*(?:一个|一名|个)?\s*(?:现实中的)?(?:真人|人类|现实中的人)", value)
    if affirms_human and not denies_human:
        return False
    if re.search(r"(?:不是|并非|并不是|不算|非)\s*(?:一个|一名)?\s*(?:AI|人工智能)", value, re.IGNORECASE):
        return False
    if re.search(r"AI|人工智能", question, re.IGNORECASE):
        if not re.search(r"(?:我是|作为|属于|确实是|本身是|是)(?:个|一名)?(?:AI|人工智能)", value, re.IGNORECASE):
            return False
    if re.search(r"真人|人类|现实中的人|你是人", question):
        denies_human = re.search(r"(?:不是|并非|并不是|不算|不是现实中的)\s*(?:一个|一名)?\s*(?:现实中的)?(?:真人|人类|现实中的人)", value)
        affirms_ai = re.search(r"(?:我是|作为|属于|确实是|本身是|是)(?:个|一名)?(?:AI|人工智能)", value, re.IGNORECASE)
        if not (denies_human or affirms_ai):
            return False
    return True


def apply_character_identity(
    event: Any, response: Any, guard: GroupReplyGuard | None = None,
) -> bool:
    """校验并在必要时纠正已唤醒的身份答复，不创建消息、不改变唤醒与权限。"""
    raw = _raw(event)
    getter = getattr(event, "get_platform_name", None)
    platform = str(getter() if callable(getter) else "").casefold()
    sender = raw.get("sender") or {}
    if (not any(name in platform for name in ("qq", "aiocqhttp", "onebot"))
            or raw.get("post_type", "message") != "message"
            or raw.get("message_type") not in {"group", "private"}
            or str(raw.get("user_id", "")) == str(raw.get("self_id", ""))
            or (isinstance(sender, dict) and sender.get("is_bot") is True)
            or _is_command(event) or getattr(response, "role", "") != "assistant"
            or getattr(response, "is_chunk", False)
            or any(getattr(response, attr, None) for attr in ("tools_call_name", "tools_call_args", "tools_call_ids"))):
        return False
    text = _prompt(event)
    if identity_question_kind(text) != "character":
        return False
    chain = getattr(getattr(response, "result_chain", None), "chain", ()) or ()
    if any(_component_type(part) not in {"plain", "at", "reply"} for part in chain):
        return False
    existing = str(getattr(response, "completion_text", "") or "").strip()
    if _is_natural_truthful_character_intro(existing, text):
        if guard is not None:
            guard.permit_once(event, existing)
        return True
    introduction = CHARACTER_INTRODUCTION
    if re.search(r"AI|人工智能", text, re.IGNORECASE):
        introduction = "我是ChatGPT的龙女仆，是AI。"
    elif re.search(r"真人|人类|现实中的人|你是人", text):
        introduction = "不是，我是ChatGPT的龙女仆，是AI。"
    response.completion_text = introduction
    if hasattr(response, "reasoning_content"):
        response.reasoning_content = None
    if guard is not None:
        guard.permit_once(event, introduction)
    return True

