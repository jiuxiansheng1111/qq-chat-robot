import re


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
    """Return the winning normalized choice, or 平局."""
    if left == right:
        return "平局"
    return _RPS_BEATS.get((left, right)) or _RPS_BEATS.get((right, left)) or ""


def _extract_rps_choices(text: str) -> list[str]:
    lowered = str(text or "").casefold()
    matches: list[tuple[int, str]] = []
    for alias, normalized in _RPS_ALIASES.items():
        for match in re.finditer(re.escape(alias), lowered):
            matches.append((match.start(), normalized))
    matches.sort(key=lambda item: item[0])

    values: list[str] = []
    for _, value in matches:
        if not values or values[-1] != value:
            values.append(value)
    return values


def resolve_rps_logic(text: str) -> str | None:
    """Answer deterministic rock-paper-scissors comparison questions."""
    compact = re.sub(r"\s+", "", str(text or "")).casefold()
    if not compact:
        return None

    choices = _extract_rps_choices(compact)
    # The bare game name naturally contains all three choices; it is not a
    # comparison unless the user explicitly asks which pair wins.
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
