_RPS_ALIASES = {
    "石头": "石头",
    "石": "石头",
    "拳头": "石头",
    "rock": "石头",
    "剪刀": "剪刀",
    "scissors": "剪刀",
    "scissor": "剪刀",
    "布": "布",
    "纸": "布",
    "paper": "布",
}

_RPS_BEATS = {
    ("石头", "剪刀"): "石头",
    ("剪刀", "布"): "剪刀",
    ("布", "石头"): "布",
}


def rps_winner(left: str, right: str) -> str:
    """返回规范化后的获胜选项；平局时返回“平局”。"""
    if left == right:
        return "平局"
    return _RPS_BEATS.get((left, right)) or _RPS_BEATS.get((right, left)) or ""






def _extract_rps_choices(text: str) -> list[str]:
    lowered = str(text or "").casefold()
    matches: list[tuple[int, str]] = []
    for alias, normalized in _RPS_ALIASES.items():
        start = 0
        while True:
            index = lowered.find(alias, start)
            if index < 0:
                break
            matches.append((index, normalized))
            start = index + max(1, len(alias))
    matches.sort(key=lambda item: item[0])

    values: list[str] = []
    for _, value in matches:
        if not values or values[-1] != value:
            values.append(value)
    return values


def resolve_rps_logic(text: str) -> str | None:
    """回答石头剪刀布这类确定性的胜负比较问题。"""
    compact = "".join(str(text or "").casefold().split())
    if not compact:
        return None

    choices = _extract_rps_choices(compact)
    # 单独的游戏名自然会同时包含三种选项；
    # 除非用户明确比较哪两种，否则不算比较请求。
    if len(set(choices)) != 2:
        return None
    if not any(
        marker in compact
        for marker in (
            "谁赢",
            "谁会赢",
            "哪个赢",
            "哪个会赢",
            "谁克",
            "克制",
            "打得过",
            "能赢",
            "会赢",
            "vs",
            "对上",
            "碰上",
        )
    ):
        return None

    left, right = choices[0], choices[1]
    winner = rps_winner(left, right)
    if winner == "平局":
        return f"{left}对{right}是平局。"
    loser = right if winner == left else left
    return f"{winner}赢，{winner}克{loser}。"
