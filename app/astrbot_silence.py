"""识别内部不回复标记及历史变体，绝不把控制文字当作聊天正文。"""

from __future__ import annotations

import re
import unicodedata

NO_REPLY_MARKER = "[QQCHAT_NO_REPLY]"


def _control_value(text: str) -> str:
    text = re.sub(r"^\s*```(?:text|plaintext)?\s*([\s\S]*?)\s*```\s*$", r"\1", str(text or ""), flags=re.IGNORECASE)
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    value = "".join(char for char in value if not char.isspace() and unicodedata.category(char) != "Cf")
    while value and unicodedata.category(value[0])[0] in {"P", "S"}:
        value = value[1:]
    while value and unicodedata.category(value[-1])[0] in {"P", "S"}:
        value = value[:-1]
    return value


def is_silence_control_text(text: str) -> bool:
    """只接受整条控制文字，正常句子里提到安静不会被删除。"""
    return _control_value(text) in {"保持安静", "qqchat_no_reply"}


def is_internal_silence_marker(text: str) -> bool:
    return _control_value(text) == "qqchat_no_reply"


def literal_silence_requested(prompt: str) -> bool:
    """保留明确翻译、复述或输出这四个字的真实用户请求。"""
    prompt = str(prompt or "")
    if re.search(r"为什么|为何|怎么老|动不动|不要|不用|别再|别发|不许|禁止", prompt):
        return False
    target = re.search(r"保持安静|keep\s+quiet|be\s+quiet|remain\s+silent", prompt, re.IGNORECASE)
    action = re.search(r"翻译|怎么说|译成|输出|复述|原样|照着|只(?:回答|回复)|四个字|台词", prompt)
    return bool(target and action)
