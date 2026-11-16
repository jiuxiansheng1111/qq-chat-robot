"""Public Chinese archetype nicknames; family matching never substitutes deck statistics."""
import re

TWENTY_PRIEST = re.compile(r"(?<!\d)(?:20|二十)\s*(?:张(?:牌)?\s*)?脏?牧(?:师)?", re.IGNORECASE)
_NAMED = re.compile(
    r"(?<!\d)(?:宇宙)?(?:20|二十)\s*(?:张(?:牌)?\s*)?脏?牧(?:师)?|"
    r"(?:阿扎莉娜|脏)牧(?:师)?|(?:Thief|Azalina)\s+Priest|"
    r"(?:海盗|宇宙|奇数|偶数|任务|弃牌|机械|元素|克苏恩|恩佐斯|艾雅|拉法姆|兆示|圣契|野猪|青玉)"
    r"[\u4e00-\u9fff]{0,6}(?:战|法|贼|骑|德|牧|术|萨|猎|瞎|dk)", re.IGNORECASE,
)


def named_archetype(text):
    match = _NAMED.search(str(text))
    return match[0] if match else ""


def is_twenty_priest(text):
    return bool(TWENTY_PRIEST.search(str(text)))


def is_thief_priest(text):
    return is_twenty_priest(text) or bool(re.search(r"脏牧|阿扎莉娜牧|(?:thief|azalina)\s+priest", str(text), re.IGNORECASE))


def community_name(text):
    return "20" if is_twenty_priest(text) else "脏牧" if is_thief_priest(text) else text
