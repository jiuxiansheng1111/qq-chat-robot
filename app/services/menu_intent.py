"""把短小明确的菜单意图映射为现有命令。"""

import re

from app.services.character_catalog import extract_catalog_lookup

_LIMIT = 40
_COMPACT_RE = re.compile(r"[\s，。！？!?、；;：:'\"“”‘’~～（）()\[\]【】]+")
_NEGATIVE_RE = re.compile(
    r"(?:不要|别再|别|不想|不需要|不必|不用|无需|不准|不许|不能|不会|不发|不看|"
    r"不关|不开|不打开|不关闭|不启用|先别|先不|没必要|关不了|关不掉|关不上)"
)
_REQUEST_WORDS = (
    "给我看看", "让我看看", "帮我看看", "帮我看", "让我看", "给我看", "看看", "看一下", "查看", "想看", "要看",
    "想要", "我要", "给我", "发给我", "发我", "发一份", "发一张", "发张", "发一下",
    "发下", "发个", "帮我发", "给我发", "来一份", "来一个", "来份", "来个", "来张", "给个",
    "显示", "列一下", "列出", "查询", "查找", "查一下", "帮我查", "告诉我", "能不能",
    "可以吗", "有什么", "怎么用", "是什么", "是啥", "想查",
)
_TEXT_MENU_WORDS = ("文字版菜单", "文字菜单", "文字版帮助", "菜单文字版")
_HELP_WORDS = ("帮助", "帮忙", "菜单", "功能菜单", "功能", "你会什么", "你能干嘛", "能干嘛", "指令")
_VOICE_ON = ("启动语音", "开启语音", "打开语音", "语音开启", "语音开一下", "开语音")
_VOICE_OFF = (
    "关闭语音", "语音关闭", "关闭语音模式", "语音关一下", "语音关掉", "语音关了",
    "关一下语音", "关一下", "关掉语音", "关语音", "停止语音", "停用语音",
)
_VOICE_CLOSE_ACTIONS = (
    "关闭语音", "语音关闭", "关闭语音模式", "语音关一下", "语音关掉",
    "关一下语音", "关一下", "关掉语音", "关语音", "停止语音", "停用语音",
)
_VOICE_STATUS = (
    "语音状态", "语音开了吗", "语音开着吗", "语音关了吗", "语音有没有开",
    "有没有开启语音", "现在语音开着吗",
)
_VOICE_LIST = ("可用角色", "角色列表", "可选角色", "语音角色列表")
_VOICE_LIST_COMMANDS = {
    "/可用角色", "可用角色", "/角色列表", "角色列表", "/音色列表", "音色列表",
    "/切换音色", "切换音色", "/选择音色", "选择音色", "/选择角色", "选择角色",
    "/切换角色", "切换角色",
}
_VOICE_TOGGLE_COMMANDS = {
    "/开启语音", "开启语音", "/启动语音", "启动语音", "/语音开启", "语音开启",
    "/打开语音", "打开语音", "/打开语音模式", "打开语音模式", "/启动语音模式",
    "启动语音模式", "/关闭语音", "关闭语音", "/语音关闭", "语音关闭",
    "/关闭语音模式", "关闭语音模式", "/语音状态", "语音状态",
}
_IMAGE_COMMANDS = {
    "/猫", "/cat", "猫图", "随机猫", "随机猫咪", "随机猫图",
    "/小猪", "/pig", "猪图", "随机猪", "随机猪猪", "随机小猪",
    "/奶龙", "奶龙", "随机奶龙", "来只奶龙", "龙来",
}
_HELP_COMMANDS = {
    "/help", "help", "/帮助", "帮助", "/菜单", "菜单", "/功能菜单", "功能菜单",
}
_TEXT_MENU_COMMANDS = {f"/{word}" for word in _TEXT_MENU_WORDS} | set(_TEXT_MENU_WORDS)
_SELECTION_PREFIXES = (
    "选择角色 ", "/选择角色 ", "切换角色 ", "/切换角色 ",
    "选择音色 ", "/选择音色 ", "切换音色 ", "/切换音色 ",
)
_SINGING_CLIP_RE = re.compile(
    r"^(?:(?:我想(?:要)?|帮我|请|麻烦|给我|让我)\s*)?"
    r"(?:(?:来|唱)(?:一)?段(?:翻唱|歌)|(?:来|唱)几句(?:翻唱|歌))(?P<tail>.*)$"
)


def _compact(text: str) -> str:
    return _COMPACT_RE.sub("", text).casefold()


def _has_request(compact: str) -> bool:
    return any(word in compact for word in _REQUEST_WORDS)


def _has_negative(compact: str) -> bool:
    return _NEGATIVE_RE.search(compact) is not None


def _command_for_input(raw: str, command: str, slash_command: str | None = None) -> str:
    if raw.startswith("/"):
        return "/" + (slash_command or command)
    return command


def _parameter_command(raw: str) -> bool:
    """保留有参数的原命令，尤其是角色名。"""
    if extract_catalog_lookup(raw) is not None:
        return True
    return any(raw.startswith(prefix) and raw[len(prefix):].strip() for prefix in _SELECTION_PREFIXES)


def _requested_catalog(raw: str, compact: str) -> str | None:
    choices = (
        ("奥特曼图鉴", "奥特曼图鉴", "查询奥特曼"),
        ("特摄角色图鉴", "特摄角色图鉴", "查询奥特曼"),
        ("二次元角色图鉴", "二次元角色图鉴", "查询二次元角色"),
        ("日漫角色图鉴", "日漫角色图鉴", "查询二次元角色"),
        ("角色图鉴", "角色图鉴", "角色图鉴"),
        ("图鉴", "角色图鉴", "角色图鉴"),
    )
    for keyword, overview_command, query_command in choices:
        if keyword not in raw or not _has_request(compact):
            continue
        tail = raw.split(keyword, 1)[1].strip(" ：:，,。！？!?、")
        tail = re.sub(r"^(?:(?:里|中|里面)?的|查找|查询|查|看|搜|找|有没有|能不能查到)\s*", "", tail)
        if tail in {"怎么用", "如何使用", "是什么", "有哪些", "有谁"}:
            tail = ""
        command = query_command if tail else overview_command
        return f"{command} {tail}".strip()
    return None


def normalize_menu_intent(text: str) -> str:
    """把不超过 40 字的明确短请求改成主路由可识别的命令。"""
    if not isinstance(text, str):
        return ""
    raw = text.strip()
    if not raw:
        return raw
    if raw in _HELP_COMMANDS | _TEXT_MENU_COMMANDS | _VOICE_LIST_COMMANDS | _VOICE_TOGGLE_COMMANDS | _IMAGE_COMMANDS:
        return raw
    if _parameter_command(raw):
        return raw
    if len(raw) > _LIMIT:
        return raw

    compact = _compact(raw.removeprefix("/"))
    phrase = raw.removeprefix("/")
    if not compact or _has_negative(compact):
        return raw

    if any(word in compact for word in _TEXT_MENU_WORDS) and _has_request(compact):
        return _command_for_input(raw, "文字版菜单")
    if compact in {"帮忙", "功能", "你会什么", "你能干嘛", "能干嘛", "有什么功能", "有哪些功能", "指令", "命令"}:
        return _command_for_input(raw, "帮助", "帮助")
    if any(word in compact for word in _HELP_WORDS) and _has_request(compact):
        return _command_for_input(raw, "帮助", "帮助")
    if "语音" in compact:
        if any(word in compact for word in _VOICE_CLOSE_ACTIONS):
            return _command_for_input(raw, "关闭语音")
        if any(word in compact for word in _VOICE_STATUS):
            return _command_for_input(raw, "语音状态")
        if any(word in compact for word in _VOICE_OFF):
            return _command_for_input(raw, "关闭语音")
        if any(word in compact for word in _VOICE_ON):
            return _command_for_input(raw, "启动语音")

    clip = _SINGING_CLIP_RE.search(phrase)
    if clip:
        tail = clip.group("tail").strip(" ，,。！？!?、:：")
        tail = re.sub(r"^(?:一下|吧|呗|啦|嘛|好吗)\s*", "", tail)
        if tail in {"一下", "吧", "呗", "啦", "嘛", "好吗"}:
            tail = ""
        command = f"翻唱片段 {tail}".strip()
        return _command_for_input(raw, command, f"翻唱片段 {tail}".strip())

    catalog = _requested_catalog(raw, compact)
    if catalog is not None:
        return _command_for_input(raw, catalog)

    if any(word in compact for word in _VOICE_LIST) and _has_request(compact):
        return _command_for_input(raw, "可用角色")

    selection = re.search(
        r"^(?:(?:请|麻烦|帮我|让我|我想|我要)\s*)?"
        r"(?:选择角色|选角色|切换(?:到|成|为)?(?:角色)?)\s*[:：]?\s*(.+)$",
        phrase,
    )
    if selection:
        name = selection.group(1).strip(" ，,。！？!?、")
        if name and name not in {"列表", "角色列表", "可用角色"}:
            return _command_for_input(raw, f"选择角色 {name}")

    if _has_request(compact):
        if "猫" in compact and any(word in compact for word in ("动图", "gif", "猫图", "图片", "照片")):
            return _command_for_input(raw, "随机猫咪", "猫")
        if any(word in compact for word in ("小猪", "猪图", "猪猪")) and any(
            word in compact for word in ("图片", "照片", "来个", "发", "看看", "随机")
        ):
            return _command_for_input(raw, "随机猪猪", "小猪")
        if "奶龙" in compact and any(
            word in compact for word in ("图片", "照片", "表情", "动图", "来个", "发", "看看", "随机")
        ):
            return _command_for_input(raw, "随机奶龙", "奶龙")

    return raw
