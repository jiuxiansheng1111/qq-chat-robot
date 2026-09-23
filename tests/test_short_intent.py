from app.services.short_intent import canonicalize_short_command


def test_affection_short_phrases_normalize():
    cases = {
        "好感": "好感度",
        "看看好感度": "好感度",
        "查下好感": "好感度",
        "我的好感多少": "好感度",
        "现在好感度多少": "好感度",
        "好感变化": "好感度记录",
        "看看最近好感变化": "好感度记录",
        "好感度历史": "好感度记录",
        "重置一下好感度": "重置好感度",
    }
    for phrase, expected in cases.items():
        assert canonicalize_short_command(phrase, addressed=True) == expected


def test_collection_and_status_short_phrases_normalize():
    cases = {
        "今天抽个奥特曼": "今日奥特曼",
        "看看我的奥特曼收藏": "我的奥特曼",
        "全部奥特曼": "奥特曼图鉴",
        "抽个二次元角色": "随机二次元角色",
        "看看我的二次元收藏": "我的二次元角色",
        "全部二次元角色": "二次元角色图鉴",
        "现在夺舍谁": "夺舍状态",
        "结束夺舍": "退出夺舍",
        "随机附身": "随机夺舍",
        "看看身份记忆": "身份记忆",
        "删掉身份记忆": "清除身份记忆",
        "记忆开着吗": "/记忆状态",
        "功能菜单": "帮助",
        "你会什么": "帮助",
    }
    for phrase, expected in cases.items():
        assert canonicalize_short_command(phrase, addressed=True) == expected


def test_parameter_commands_preserve_payload():
    cases = {
        "搜一下 Python 3.13": "搜索 Python 3.13",
        "帮我查 今天天气": "搜索 今天天气",
        "翻译一下 hello world": "翻译 hello world",
        "来一首 晴天": "点歌 晴天",
        "搜个视频 迪迦 最终圣战": "播放视频 迪迦 最终圣战",
    }
    for phrase, expected in cases.items():
        assert canonicalize_short_command(phrase, addressed=True) == expected


def test_unaddressed_or_long_chat_is_not_hijacked():
    assert canonicalize_short_command("看看好感度", addressed=False) == "看看好感度"
    long_chat = "我最近在想奥特曼这个系列为什么会有这么多不同作品和世界观设定"
    assert canonicalize_short_command(long_chat, addressed=True) == long_chat
    assert canonicalize_short_command("我想看看奥特曼剧情", addressed=True) == "我想看看奥特曼剧情"
