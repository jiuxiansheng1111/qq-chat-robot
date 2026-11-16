"""QQ setting receipts stay brief; queries and failed actions keep their details."""

from __future__ import annotations

import re
from collections.abc import Mapping

SIMPLE_SETTING_ACK = "好的，我知道了"
_SETTING_COMMAND = re.compile(
    r"^/?(?:选择音色|切换音色|启动语音|开启语音|关闭语音|开启恋爱模式|关闭恋爱模式|"
    r"设置格式|设置模板)(?:\s|$)|"
    r"^/?(?:陪伴群?|表情管理)\s+(?:开启|关闭|启用|停用|设置|切换|选择)"
)
_SUCCESS = re.compile(
    r"^(?:[✅✔☑]\ufe0f?\s*)?(?:[^\n]{0,32}已(?:设置|切换|开启|关闭|启用|停用|修改|保存|更新|选定|选择|绑定)|"
    r"(?:设置|切换)(?:成功|好了))"
)
_FAILURE = re.compile(r"失败|无权限|权限不足|不支持|不可用|未配置|尚未配置|无法|不能|找不到|没有找到")


def setting_ack(event: Mapping, detailed: str, *, notice: str = "") -> str:
    """The legacy QQ command handler also serves isolated WeChat routes."""
    if "wechat_private" in event:
        return detailed
    return SIMPLE_SETTING_ACK + ("\n" + notice if notice else "")


def is_simple_setting_success(command: str, text: str) -> bool:
    return bool(_SETTING_COMMAND.search(command.strip()) and _SUCCESS.search(text.strip()) and not _FAILURE.search(text))


def simplify_qq_setting_result(event) -> bool:
    from app.astrbot_qq_persona import is_qq

    if not is_qq(event):
        return False
    result = event.get_result()
    if result is None or result.is_llm_result():
        return False
    chain = getattr(result, "chain", ())
    if not chain or any(type(part).__name__ not in {"Plain", "Reply", "At"} for part in chain):
        return False
    text = "".join(str(getattr(part, "text", "")) for part in chain if type(part).__name__ == "Plain")
    raw = getattr(getattr(event, "message_obj", None), "raw_message", {})
    raw_text = "".join(str(part.get("data", {}).get("text", "")) for part in raw.get("message", ())
                       if isinstance(part, Mapping) and part.get("type") == "text") if isinstance(raw, Mapping) else ""
    command = (event.get_extra("qqchat_natural_command") or raw_text
               or event.get_extra("astrbot_original_message_str") or event.get_message_str())
    if not is_simple_setting_success(str(command), text):
        return False
    from astrbot.api.message_components import Plain

    result.chain = [Plain(SIMPLE_SETTING_ACK)]
    event.set_extra("qqchat_simple_setting_ack", True)
    return True
