import re


_SHORT_LIMIT = 36
_PUNCTUATION_RE = re.compile(r"[\s，。！？!?、；;：:'\"“”‘’~～（）()\[\]【】]+")
_VIEW_WORDS = ("看", "查看", "看看", "看下", "看一下", "查", "查下", "查询", "多少", "几", "当前", "现在", "我的")
_CLEAR_WORDS = ("清除", "删除", "删掉", "删了", "忘掉", "忘记")
_HISTORY_WORDS = ("记录", "历史", "变化", "变动", "升降", "涨跌", "最近")
_RANDOM_WORDS = ("随机", "抽", "抽个", "抽一个", "来个", "来一个", "今天", "今日")
_CATALOG_WORDS = ("图鉴", "全部", "所有", "大全", "列表")


def _compact(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("/"):
        value = value[1:]
    return _PUNCTUATION_RE.sub("", value).casefold()


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _strip_polite_prefix(text: str) -> str:
    value = str(text or "").strip()
    prefixes = (
        "帮我",
        "麻烦",
        "请",
        "给我",
        "能不能",
        "可以",
        "能否",
        "我要",
        "我想",
    )
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if value.startswith(prefix):
                value = value[len(prefix) :].lstrip(" ，,：:")
                changed = True
                break
    return value


def canonicalize_short_command(text: str, *, addressed: bool) -> str:
    """Map natural short phrases onto existing command strings.

    Only explicitly addressed messages (bot mention) or slash commands are
    normalized. Long conversational messages are deliberately left untouched.
    """
    raw = str(text or "").strip()
    if not raw:
        return raw
    if not addressed and not raw.startswith("/"):
        return raw

    compact = _compact(raw)
    if not compact or len(compact) > _SHORT_LIMIT:
        return raw

    # Affection
    if "好感" in compact:
        if _contains_any(compact, ("重置", "恢复初始", "初始化")):
            return "重置好感度"
        if _contains_any(compact, _HISTORY_WORDS) or "怎么涨" in compact or "怎么掉" in compact:
            return "好感度记录"
        if (
            compact in {"好感", "好感度"}
            or _contains_any(compact, _VIEW_WORDS)
            or compact.endswith(("多少", "几", "呢", "呀", "啊"))
        ):
            return "好感度"

    # Memory / identity controls. Do not rewrite "记住..." because it carries data.
    if "身份" in compact and "记忆" in compact:
        if _contains_any(compact, _CLEAR_WORDS):
            return "清除身份记忆"
        if _contains_any(compact, _VIEW_WORDS) or compact in {"身份记忆", "我的身份记忆"}:
            return "身份记忆"

    if "长期记忆" in compact or "你记得我什么" in compact or "记得我什么" in compact:
        if _contains_any(compact, _CLEAR_WORDS):
            return "清除长期记忆"
        if _contains_any(compact, _VIEW_WORDS) or "记得我什么" in compact:
            return "我的长期记忆"

    if "群记忆" in compact:
        if _contains_any(compact, _CLEAR_WORDS):
            return "清除群记忆"
        if _contains_any(compact, _VIEW_WORDS) or compact == "群记忆":
            return "群记忆"

    if "记忆" in compact and not compact.startswith(("记住", "让你记住")):
        if _contains_any(compact, ("状态", "开没开", "开着吗", "开启了吗", "有没有开")):
            return "/记忆状态"
        if _contains_any(compact, ("开启", "打开", "开记忆")):
            return "/记忆开启"
        if _contains_any(compact, ("关闭", "关掉", "关记忆")):
            return "/记忆关闭"
        if _contains_any(compact, ("清空短期", "删除短期", "清除短期")):
            return "/记忆删除"

    # Possession
    if "夺舍" in compact or "附身" in compact:
        if _contains_any(compact, ("退出", "结束", "解除", "取消")):
            return "退出夺舍"
        if _contains_any(compact, ("状态", "现在是谁", "现在夺舍谁", "夺舍谁")):
            return "夺舍状态"
        if _contains_any(compact, _RANDOM_WORDS):
            return "随机夺舍"

    # Ultraman
    if "奥特曼" in compact:
        if _contains_any(compact, _CATALOG_WORDS):
            return "奥特曼图鉴"
        if _contains_any(compact, ("收藏", "我的", "本命", "收集")):
            return "我的奥特曼"
        if _contains_any(compact, _RANDOM_WORDS):
            return "今日奥特曼"

    # Anime character collection
    if "二次元" in compact or "二次元角色" in compact:
        if _contains_any(compact, _CATALOG_WORDS):
            return "二次元角色图鉴"
        if _contains_any(compact, ("收藏", "我的", "本命", "收集")):
            return "我的二次元角色"
        if _contains_any(compact, _RANDOM_WORDS):
            return "随机二次元角色"

    # Help/menu
    if compact in {
        "帮助",
        "帮忙",
        "菜单",
        "功能",
        "功能菜单",
        "你会什么",
        "你能干嘛",
        "能干嘛",
        "有什么功能",
        "有哪些功能",
        "指令",
        "命令",
    }:
        return "帮助"

    # Casual image request aliases.
    if "奶龙" in compact and _contains_any(compact, ("图", "图片", "来张", "来个", "随机")):
        return "随机奶龙"
    if "猫" in compact and _contains_any(compact, ("猫图", "图片", "来张", "来个", "随机")):
        return "随机猫咪"
    if ("猪" in compact or "小猪" in compact) and _contains_any(
        compact, ("猪图", "图片", "来张", "来个", "随机")
    ):
        return "随机猪猪"

    # Parameter-carrying commands: normalize only the action prefix and preserve
    # the payload so existing extractors still receive the user's query.
    stripped = _strip_polite_prefix(raw.lstrip("/"))
    prefix_patterns = (
        (r"^(?:搜一下|搜下|搜索一下|搜索下|查一下|查下|帮我搜|帮我查)\s*(.+)$", "搜索"),
        (r"^(?:翻译一下|翻译下|帮我翻译)\s*(.+)$", "翻译"),
        (r"^(?:点一首|点首|来一首|来首|点歌一下)\s*(.+)$", "点歌"),
        (r"^(?:找个视频|找视频|搜个视频|搜视频|播放一下视频)\s*(.+)$", "播放视频"),
    )
    for pattern, command in prefix_patterns:
        match = re.match(pattern, stripped, re.IGNORECASE)
        if match and match.group(1).strip():
            return f"{command} {match.group(1).strip()}"

    return raw
