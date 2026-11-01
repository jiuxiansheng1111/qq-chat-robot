"""Choose and explain visible decision points without using subsequent results."""

import re
from collections import Counter

from app.services.mahjong.analysis import Decision


def shape_choice(decision):
    actual = next(c for c in decision.candidates if c.id == decision.actual)
    shaped = [c for c in decision.candidates if c.shanten is not None]
    return min(shaped, key=lambda c: (c.shanten, -sum(n for _, n in c.ukeire), c.id != actual.id)) if shaped else actual


def priority(decision):
    actual = next(c for c in decision.candidates if c.id == decision.actual)
    best = shape_choice(decision)
    loss = max(0, actual.shanten - best.shanten) if actual.shanten is not None and best.shanten is not None else 0
    outs = max(0, sum(n for _, n in best.ukeire) - sum(n for _, n in actual.ukeire))
    threat = any(t is not None for s, t in enumerate(decision.view.riichi_at) if s != decision.view.seat)
    irreversible = decision.actual.startswith(("riichi:", "chi:", "pon:", "ankan:", "kakan:", "daiminkan:"))
    return (len(decision.candidates) > 1, loss * 50 + outs + threat * 25 + irreversible * 20,
            bool(decision.key_reasons), -decision.view.event)


def select_decisions(decisions: tuple[Decision, ...], *, minimum=4, maximum=32):
    """One meaningful choice per round including repeats, then fill short matches."""
    groups = {}
    for number, decision in enumerate(decisions, 1):
        groups.setdefault((decision.view.round, decision.view.honba), []).append((number, decision))
    # Every round should explain an actual discard when one can be compared.
    # Pass/chi/pon branches often have a larger shape delta but obscure tile choices.
    selected = []
    for group in groups.values():
        discards = [pair for pair in group if pair[1].actual.startswith(("discard:", "riichi:"))
                    and len(pair[1].candidates) > 1]
        selected.append(max(discards or group, key=lambda pair: priority(pair[1])))
    used = {number for number, _ in selected}
    rest = sorted(((i, d) for i, d in enumerate(decisions, 1) if i not in used),
                  key=lambda pair: priority(pair[1]), reverse=True)
    selected.extend(rest[:max(0, minimum - len(selected))])
    return sorted(selected[:maximum], key=lambda pair: pair[0])


def readable_tiles(text):
    def convert(match):
        digit, suit = match[0]
        if suit == "z":
            return ("东", "南", "西", "北", "白", "发", "中")[int(digit) - 1]
        return ("赤5" if digit == "0" else digit) + {"m": "万", "p": "筒", "s": "索"}[suit]
    return re.sub(r"[0-9][mps]|[1-7]z", convert, text)


def seat_name(view, seat):
    return "东南西北"[(seat - view.dealer) % 4] + "家" + ("（庄）" if seat == view.dealer else "")


def action_label(candidate):
    # A normal discard is not necessarily tenpai; do not label every one as dama.
    return readable_tiles(candidate.action.replace("（不杠/默听可选）", ""))


def situation(decision):
    view = decision.view
    own = view.scores[view.seat]
    rank = 1 + sum(score > own for score in view.scores)
    above = [score - own for score in view.scores if score > own]
    below = [own - score for score in view.scores if score < own]
    gap = f"距上一名{min(above)}点" if above else (f"领先下一名{min(below)}点" if below else "四家同分")
    tied = sum(score == own for score in view.scores) > 1
    standing = f"本人{own}点，{'并列' if tied else ''}第{rank}，{gap}"
    threats = [seat_name(view, i) for i, turn in enumerate(view.riichi_at) if i != view.seat and turn is not None]
    opened = [f"{seat_name(view, i)}{len(ms)}组" for i, ms in enumerate(view.melds) if i != view.seat and ms]
    danger = "/".join(threats) + "已立直" if threats else "无人明示立直，仍需留意默听"
    if opened:
        danger += "；他家副露/杠：" + "/".join(opened)
    return f"{view.phase}，牌山余{view.live_tiles}张；本人{'坐庄' if view.seat == view.dealer else '子家'}；{standing}；{danger}。"


def decision_digest(decision, number, coaching=None):
    view = decision.view
    actual = next(c for c in decision.candidates if c.id == decision.actual)
    node = next((n for n in coaching.nodes if n.node == number and n.status == "ok"), None) if coaching else None
    preferred = decision.candidates[node.preferred - 1] if node else shape_choice(decision)
    label = "GPT-6 Luna 建议" if node else "牌效候选（非攻守定论）"
    lines = [f"【{'东南西北'[view.round // 4]}{view.round % 4 + 1}局{view.honba}本场·第{view.turn}巡·节点{number}】",
             "场况：" + situation(decision),
             "手牌：" + readable_tiles(" ".join(view.hand)),
             "实际：" + action_label(actual) + "；" + label + "：" + action_label(preferred) + "。"]
    if preferred.id != actual.id:
        kept = list((Counter(preferred.hand) - Counter(actual.hand)).elements())
        lines.append("本次不优先：" + action_label(actual)
                     + ("；相比实战，这条路线保留" + readable_tiles("、".join(kept)) if kept else "") + "。")
    else:
        lines.append("这手可保留实战选择；其他分支的收益与风险仍需比较。" if node else "实战选择与牌效排序一致。")
    if actual.shanten is not None and preferred.shanten is not None:
        lines.append(f"牌效对照：实战{actual.shanten}向听／未见有效牌{sum(n for _, n in actual.ukeire)}枚；"
                     f"候选{preferred.shanten}向听／{sum(n for _, n in preferred.ukeire)}枚。")
    if node:
        lines.extend(["场况判断：" + node.situation, "理由：" + node.reason, "攻守计划：" + node.plan])
        actual_number = decision.candidates.index(actual) + 1
        actual_comparison = next((c for c in node.comparisons if c.candidate == actual_number), None)
        if actual_comparison and preferred.id != actual.id:
            lines.append("实战这手的代价：" + actual_comparison.cost)
        comparison = next((c for c in node.comparisons if c.candidate == node.preferred), None)
        if comparison:
            lines.append(f"代价：{comparison.cost.rstrip('。；')}；改主意的条件：{comparison.change_when}")
    else:
        lines.append("理由：优先比较向听较小、未见有效牌较多的分支；役、打点和安全性需另核。")
    if any(t is not None for i, t in enumerate(view.riichi_at) if i != view.seat):
        lines.append("攻守核对：" + readable_tiles(preferred.safety))
    return "\n".join(lines)


def split_report(text, limit=1800):
    """Split at paragraphs/lines without dropping the remainder of the report."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 3:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks
