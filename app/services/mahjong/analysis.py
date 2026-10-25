"""Sequential reconstruction + immutable self-visible snapshots + transparent rule baseline.

No scoring/strategy weights, Monte Carlo, neural policy, or calibrated probability model
is bundled. Shape comparisons are facts; strategic trade-offs are conditional explanations.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Protocol

from app.services.mahjong.records import RecordError, VisibleReplay
from app.services.mahjong.tiles import (
    counts,
    dora_count,
    improvements,
    index,
    name,
    riichi_ankan_allowed,
    shanten,
    waits,
)

WIND_NAMES = "东南西北"


@dataclass(frozen=True)
class Meld:
    kind: str
    tiles: tuple[str, ...]


@dataclass(frozen=True)
class RiverTile:
    tile: str
    event: int
    called: bool = False
    tsumogiri: bool | None = None


@dataclass(frozen=True)
class DecisionView:
    round: int
    event: int
    seat: int
    dealer: int
    match: str
    hand: tuple[str, ...]
    melds: tuple[tuple[Meld, ...], ...]
    rivers: tuple[tuple[RiverTile, ...], ...]
    riichi_at: tuple[int | None, ...]
    scores: tuple[int, ...]
    indicators: tuple[str, ...]
    live_tiles: int
    honba: int
    kyotaku: int
    drawn: str | None
    last_discard: tuple[int, str] | None
    kuikae: tuple[int, ...] = ()

    @property
    def turn(self) -> int:
        return len(self.rivers[self.seat]) + 1

    @property
    def phase(self) -> str:
        # Descriptive bins only, never a standalone danger or probability rule.
        if self.live_tiles <= 16 or self.turn >= 13:
            return "晚巡"
        return "早巡" if self.turn <= 6 else "中巡"


@dataclass(frozen=True)
class Candidate:
    id: str
    action: str
    hand: tuple[str, ...]
    meld_count: int
    shanten: int | None
    ukeire: tuple[tuple[str, int], ...]
    dora: int
    safety: str
    value: str
    benefit: str
    cost: str
    legality: str = "通过已实现规则校验"


@dataclass(frozen=True)
class EngineMetadata:
    name: str
    version: str
    weights_sha256: str
    license: str
    objective: str


@dataclass(frozen=True)
class EngineEstimate:
    candidate_id: str
    explanation: str


class LocalStrategyEngine(Protocol):
    """Opt-in local injection only. No loading executable paths or endpoints from records.

    This interface accepts qualitative estimates only. Numeric probabilities need a separate
    reviewed calibration/simulation interface and are intentionally not implemented here.
    """

    metadata: EngineMetadata

    def estimate(
        self, view: DecisionView, candidates: tuple[Candidate, ...]
    ) -> tuple[EngineEstimate, ...]: ...


@dataclass(frozen=True)
class Decision:
    view: DecisionView
    candidates: tuple[Candidate, ...]
    actual: str
    key_reasons: tuple[str, ...]
    advice: str
    engine: tuple[EngineEstimate, ...] = ()


@dataclass(frozen=True)
class VisibleAnalysis:
    match: str
    seat: int
    decisions: tuple[Decision, ...]
    final_hands: tuple[tuple[str, ...], ...]
    final_meld_counts: tuple[int, ...]
    final_live_tiles: tuple[int, ...]
    inspected_events: int
    engine_metadata: EngineMetadata | None = None


def visible_counts(view: DecisionView) -> tuple[int, ...]:
    known = list(view.hand) + list(view.indicators)
    for river in view.rivers:
        known.extend(t.tile for t in river if not t.called)
    for melds in view.melds:
        for meld in melds:
            known.extend(meld.tiles)
    try:
        return counts(known)
    except ValueError as exc:
        raise RecordError("本人手牌与公开牌合计超过四枚；请检查重复记牌、赤五或事件顺序") from exc


def safety(view: DecisionView, discarded: str | None) -> str:
    if discarded is None:
        return "未立即舍牌；后续舍牌与补牌未知，不能按零风险处理"
    safe, threats = [], []
    k = index(discarded)
    for actor in range(4):
        if actor == view.seat:
            continue
        reach = view.riichi_at[actor]
        if reach is not None:
            # Riichi: own discards, and passed discards since reach, imply permanent furiten.
            genbutsu = {index(t.tile) for t in view.rivers[actor]}
            genbutsu.update(
                index(t.tile) for river in view.rivers for t in river if t.event >= reach
            )
            (safe if k in genbutsu else threats).append(f"座位{actor}")
    result = []
    if safe:
        result.append("对立直" + "/".join(safe) + "为现物（通常荣和限制；不代表不被别家和）")
    if threats:
        result.append("对立直" + "/".join(threats) + "非现物，危险程度无法可靠量化")
    for actor in range(4):
        if (
            actor != view.seat
            and view.riichi_at[actor] is None
            and k in {index(t.tile) for t in view.rivers[actor]}
        ):
            result.append(f"对未立直座位{actor}为舍牌现物（该家荣和受振听限制；不保证其他家安全）")
    opened = [str(a) for a in range(4) if a != view.seat and view.melds[a]]
    if opened:
        result.append("座位" + "/".join(opened) + "已有副露，需关注役牌/染手线索与默听")
    if not result:
        result.append("无已记录立直；不代表安全，默听及后续改手仍可能")
    if k < 27:
        for actor in range(4):
            if actor == view.seat:
                continue
            river = {index(t.tile) for t in view.rivers[actor]}
            needed = [k + d for d in (-3, 3) if 0 <= k % 9 + d < 9]
            if needed and all(n in river for n in needed):
                result.append(
                    f"对座位{actor}有筋线索（仅对应两面受限制；嵌张/边张/双碰/单骑仍须警惕）"
                )
        visible = visible_counts(view)
        walls = [
            name(n)
            for n in range(max(k // 9 * 9, k - 2), min(k // 9 * 9 + 8, k + 2) + 1)
            if n != k and visible[n] == 4
        ]
        if walls:
            result.append("附近四枚壁" + "/".join(walls) + "会限制部分顺子组合，不等于绝对安全")
    else:
        result.append(f"该字牌可见未见{4 - visible_counts(view)[k]}枚；不能单凭枚数排除单骑/国士")
    return "；".join(result)


def _value(
    view: DecisionView, hand: tuple[str, ...], melds: tuple[Meld, ...], riichi: bool = False
) -> str:
    all_tiles = hand + tuple(t for m in melds for t in m.tiles)
    closed = all(m.kind == "ankan" for m in melds)
    yakuhai = 0
    held = Counter(index(t) for t in all_tiles)
    own_wind = 27 + (view.seat - view.dealer) % 4
    round_wind = 27 + view.round // 4
    for k in (31, 32, 33, own_wind, round_wind):
        yakuhai += held[k] >= 3
    pieces = [
        "门清" if closed else "副露",
        f"当前宝牌/赤牌合计{dora_count(all_tiles, view.indicators)}枚",
    ]
    if yakuhai:
        pieces.append(f"役牌刻子证据{yakuhai}番（含连风重复计番；仍须合法和形）")
    if riichi:
        pieces.append("立直成立可增加立直役1番，支出1000；一发/里宝牌未知")
    if not yakuhai and not riichi:
        pieces.append("未核验全役种；宝牌不是役，副露后尤其要确认役")
    pieces.append("未实现完整番符计分，不报确定和牌打点")
    return "；".join(pieces)


def _candidate(
    view: DecisionView,
    identifier: str,
    action: str,
    hand: tuple[str, ...],
    melds: tuple[Meld, ...],
    discarded: str | None,
    benefit: str,
    cost: str,
    *,
    riichi: bool = False,
    legality: str = "通过已实现规则校验",
) -> Candidate:
    n = len(melds)
    ready = shanten(hand, n) if len(hand) == 13 - 3 * n else None
    effective = improvements(hand, n, visible_counts(view)) if ready is not None else ()
    all_tiles = hand + tuple(t for m in melds for t in m.tiles)
    value_note = _value(view, hand, melds, riichi)
    if ready == 0:
        shape_waits = waits(hand, n)
        river = {index(t.tile) for t in view.rivers[view.seat]}
        if discarded is not None:
            river.add(index(discarded))
        value_note += "；形状待牌" + "/".join(name(k) for k in sorted(shape_waits))
        if shape_waits & river:
            value_note += "；舍牌振听：此形状不可荣和，合法自摸仍须有役；改听后需重新核验"
        value_note += "；临时/立直过水振听未完整裁定，不将形状待牌当合法荣和牌"
    return Candidate(
        identifier,
        action,
        hand,
        n,
        ready,
        effective,
        dora_count(all_tiles, view.indicators),
        safety(view, discarded),
        value_note,
        benefit,
        cost,
        legality,
    )


def _remove(hand: tuple[str, ...], used: tuple[str, ...]) -> tuple[str, ...]:
    rest = list(hand)
    try:
        for t in used:
            rest.remove(t)
    except ValueError as exc:
        raise RecordError("本人动作消耗了不在手中的牌") from exc
    return tuple(rest)


def kan_available(view: DecisionView) -> bool:
    return (
        view.live_tiles > 0
        and sum(m.kind in {"ankan", "kakan", "daiminkan"} for ms in view.melds for m in ms) < 4
    )


KAN_BENEFIT = "保留/形成刻子并获得一次岭上补牌机会；杠子符与新宝牌可能增值（都不是确定和牌收益）"
KAN_COST = (
    "新宝牌会影响四家，立直者还可能有对应里宝牌；补牌未知，鸣杠会改变巡序/海底机会并中断一发；"
    "不能复用实际后续牌序模拟不杠分支；四杠相关途中流局需依实际事件确认"
)


def own_candidates(view: DecisionView) -> tuple[Candidate, ...]:
    melds = view.melds[view.seat]
    if len(view.hand) != 14 - 3 * len(melds):
        raise RecordError("本人出牌/杠前牌数错误，缺摸牌、重复事件或副露不完整")
    result = []
    reached = view.riichi_at[view.seat] is not None
    legal_tiles = [view.drawn] if reached else sorted(set(view.hand), key=lambda t: (index(t), t))
    closed = all(m.kind == "ankan" for m in melds)
    for discarded in legal_tiles:
        if discarded is None or index(discarded) in view.kuikae:
            continue
        after = _remove(view.hand, (discarded,))
        result.append(
            _candidate(
                view,
                f"discard:{discarded}",
                f"打{discarded}" + ("（不杠/默听可选）" if not reached else "（立直后摸切）"),
                after,
                melds,
                discarded,
                "保留改手与防守选择（立直后仅摸切）",
                "舍掉的搭子/宝牌与安全牌不能再用于本手；进张最多不等于顺位最优",
            )
        )
        if (
            not reached
            and closed
            and view.scores[view.seat] >= 1000
            and view.live_tiles >= 4
            and shanten(after, len(melds)) == 0
        ):
            result.append(
                _candidate(
                    view,
                    f"riichi:{discarded}",
                    f"立直打{discarded}",
                    after,
                    melds,
                    discarded,
                    "门清听牌立直可提供1番役及一发/里宝牌机会；流局须按规则公开确认听牌",
                    "支出1000进入供托，无法自由撤退/改手；待牌振听时不能荣和，不能把机会当确定增值",
                    riichi=True,
                )
            )
    if kan_available(view):
        for k, amount in enumerate(counts(view.hand)):
            if amount == 4:
                used = tuple(t for t in view.hand if index(t) == k)
                if reached and not riichi_ankan_allowed(view.hand, view.drawn, name(k), len(melds)):
                    continue
                new_melds = melds + (Meld("ankan", used),)
                result.append(
                    _candidate(
                        view,
                        f"ankan:{name(k)}",
                        f"暗杠{name(k)}",
                        _remove(view.hand, used),
                        new_melds,
                        None,
                        KAN_BENEFIT + "；暗杠仍保持门清",
                        KAN_COST + "；四张牌固定后改良与退路减少；暗杠抢杠仅特殊国士例外需完整裁定",
                        legality="四枚/补牌/杠数已核验；立直后另核验等听张且所有分解中刻子固定",
                    )
                )
        if not reached:
            for old in melds:
                k = index(old.tiles[0])
                if old.kind == "pon":
                    for added in sorted({t for t in view.hand if index(t) == k}):
                        updated = tuple(
                            Meld("kakan", old.tiles + (added,)) if m is old else m for m in melds
                        )
                        result.append(
                            _candidate(
                                view,
                                f"kakan:{added}",
                                f"加杠{added}",
                                _remove(view.hand, (added,)),
                                updated,
                                None,
                                KAN_BENEFIT,
                                KAN_COST + "；加杠当场可能被抢杠，且失去该张用于防守的选择",
                            )
                        )
    return tuple(result)


def _consumptions(hand: tuple[str, ...], required: tuple[int, ...]) -> tuple[tuple[str, ...], ...]:
    if not required:
        return ((),)
    found = set()
    for t in set(hand):
        if index(t) == required[0]:
            for tail in _consumptions(_remove(hand, (t,)), required[1:]):
                found.add(tuple(sorted((t,) + tail)))
    return tuple(sorted(found))


def forbidden_after_call(kind: str, taken: str, used: tuple[str, ...]) -> tuple[int, ...]:
    k = index(taken)
    banned = {k}  # Same-tile kuikae for chi and pon.
    if kind == "chi":
        numbers = sorted(index(t) for t in used + (taken,))
        if k == numbers[0] and k % 9 <= 5:
            banned.add(k + 3)
        if k == numbers[-1] and k % 9 >= 3:
            banned.add(k - 3)
    return tuple(sorted(banned))


def response_candidates(view: DecisionView) -> tuple[Candidate, ...]:
    if view.last_discard is None:
        raise RecordError("缺少鸣牌对应的舍牌")
    actor, taken = view.last_discard
    melds = view.melds[view.seat]
    if len(view.hand) != 13 - 3 * len(melds):
        raise RecordError("他家舍牌时本人牌数不正确")
    result = [
        _candidate(
            view,
            "pass",
            "不鸣/不杠",
            view.hand,
            melds,
            None,
            "保留门清或现有手牌与安牌，不为四家增加杠宝牌",
            "可能错过速度或役牌机会；不能断言下一摸一定更好",
        )
    ]
    if view.riichi_at[view.seat] is not None:
        return tuple(result)  # Ron/yaku/furiten adjudication is intentionally not automated.
    k = index(taken)
    options = [("pon", (k, k))]
    if kan_available(view):
        options.append(("daiminkan", (k, k, k)))
    if actor == (view.seat - 1) % 4 and k < 27:
        for start in range(max(k // 9 * 9, k - 2), min(k, k // 9 * 9 + 6) + 1):
            options.append(("chi", tuple(i for i in range(start, start + 3) if i != k)))
    for kind, required in options:
        for used in _consumptions(view.hand, required):
            after = _remove(view.hand, used)
            new_melds = melds + (Meld(kind, used + (taken,)),)
            identifier = f"{kind}:{','.join(used)}"
            if kind == "daiminkan":
                result.append(
                    _candidate(
                        view,
                        identifier,
                        f"大明杠{taken}",
                        after,
                        new_melds,
                        None,
                        KAN_BENEFIT,
                        KAN_COST + "；破门清，缩短安全牌储备；大明杠非通常抢杠对象，原舍牌荣和优先",
                    )
                )
                continue
            banned = forbidden_after_call(kind, taken, used)
            # Each call branch includes its immediate legal discard, rather than hiding its cost.
            for discarded in sorted(set(after), key=lambda t: (index(t), t)):
                if index(discarded) in banned:
                    continue
                result.append(
                    _candidate(
                        view,
                        identifier + f"/discard:{discarded}",
                        f"{'吃' if kind == 'chi' else '碰'}{taken}后打{discarded}",
                        _remove(after, (discarded,)),
                        new_melds,
                        discarded,
                        "固定面子，可对照鸣后向听/进张判断是否真的提速；役牌碰可提供役的证据",
                        "破门清/损失门清役与食下，暴露信息、手牌变短与退路减少；需核验至少一役",
                    )
                )
    return tuple(result)


def ranking(view: DecisionView) -> str:
    own = view.scores[view.seat]
    gaps = "；".join(f"对座位{s}差{view.scores[s] - own:+d}" for s in range(4) if s != view.seat)
    nominal_last = view.round >= (3 if view.match == "tonpuu" else 7)
    timing = "名义末局或延长局" if nominal_last else "仍有名义剩余局"
    return (
        f"四家当前点数{view.scores}，{gaps}；{timing}，本场{view.honba}/供托{view.kyotaku}根。"
        "点差是净分差条件，直击会双向改变差额，自摸/横移不同；"
        "同分排序、连庄止/西入/飞人未裁定，不能把点差直接当所需和牌点数"
    )


def _finish(
    view: DecisionView,
    candidates: tuple[Candidate, ...],
    actual: str,
    engine: LocalStrategyEngine | None,
) -> Decision:
    selected = next((c for c in candidates if c.id == actual), None)
    if selected is None:
        raise RecordError(
            "实际动作未通过已实现的合法候选检查（可能缺数据/不支持规则），不能强行评分"
        )
    reasons = []
    if actual.startswith(("riichi", "chi", "pon", "ankan", "kakan", "daiminkan")):
        reasons.append("立直/鸣牌/杠牌的不可逆取舍")
    if any(c.id.startswith(("ankan", "kakan", "daiminkan")) for c in candidates):
        reasons.append("存在杠与不杠分支")
    threats = any(t is not None for s, t in enumerate(view.riichi_at) if s != view.seat)
    if threats:
        reasons.append("他家立直后的攻守转换")
    shaped = [c for c in candidates if c.shanten is not None]
    best = (
        min(shaped, key=lambda c: (c.shanten, -sum(n for _, n in c.ukeire))) if shaped else selected
    )
    if selected.shanten is not None and best.shanten is not None:
        if selected.shanten > best.shanten:
            reasons.append("实际选择增加向听，需说明防守/价值条件")
        elif sum(n for _, n in best.ukeire) - sum(n for _, n in selected.ukeire) >= 4:
            reasons.append("形状进张差至少4枚，但不能仅凭枚数判错")
    if view.phase == "晚巡":
        reasons.append("晚巡保听与弃听的流局收益条件")
    advice = (
        f"证据不足，按目标比较：形状基线可优先核对「{best.action}」"
        "（只按向听较小、可见形状进张较多排序，不是最优策略或胜率）。"
        "若有立直威胁且有现物，保留防守候选；若需要追分，核对役与达标打点后再决定是否推进。"
        "没有完整计分与对手模型，不对速度/价值/安全作统一最优承诺。"
    )
    estimates: tuple[EngineEstimate, ...] = ()
    if engine is not None:
        estimates = engine.estimate(view, candidates)
        if not isinstance(estimates, tuple) or any(
            type(e) is not EngineEstimate
            or e.candidate_id not in {c.id for c in candidates}
            or not isinstance(e.explanation, str)
            or len(e.explanation) > 1000
            for e in estimates
        ):
            raise RecordError("本地引擎返回格式不受支持；概率/任意数值输出未开放")
    return Decision(view, candidates, actual, tuple(reasons), advice, estimates)


def analyze_visible(
    replay: VisibleReplay, *, engine: LocalStrategyEngine | None = None
) -> VisibleAnalysis:
    if type(replay) is not VisibleReplay:
        raise TypeError("决策模块只接受已隔离的 VisibleReplay")
    if engine is not None:
        m = engine.metadata
        if (
            type(m) is not EngineMetadata
            or not all((m.name, m.version, m.license, m.objective))
            or len(m.weights_sha256) != 64
            or any(c not in "0123456789abcdef" for c in m.weights_sha256)
        ):
            raise RecordError("可选引擎缺少版本、合法权重摘要、许可证或优化目标")
    decisions = []
    final_hands = []
    final_melds = []
    final_live = []
    inspected = 0
    seat = replay.seat
    for raw in replay.rounds:
        view = DecisionView(
            raw["round"],
            0,
            seat,
            raw["dealer"],
            replay.match,
            raw["own_hand"],
            ((), (), (), ()),
            ((), (), (), ()),
            (None, None, None, None),
            raw["scores"],
            raw["dora_indicators"],
            70,
            raw["honba"],
            raw["kyotaku"],
            None,
            None,
        )
        visible_counts(view)
        next_draw = view.dealer
        need_discard: int | None = None
        replacement_for: int | None = None
        pending: tuple[DecisionView, tuple[Candidate, ...]] | None = None
        pending_call: tuple[DecisionView, tuple[Candidate, ...], str] | None = None
        ended = False
        accepted_reaches: set[int] = set()
        for event_no, event in enumerate(raw["events"], 1):
            inspected += 1
            kind = event["type"]
            if ended:
                # Multiple ron records are allowed, but no later play after a win.
                if kind != "ron":
                    raise RecordError("终局后仍有摸打事件")
                continue
            if pending is not None and kind not in {"dora", "riichi_accept"}:
                pv, pc = pending
                if event.get("actor") == seat and kind in {"chi", "pon", "daiminkan"}:
                    used = tuple(sorted(event["consumed"]))
                    prefix = f"{kind}:{','.join(used)}"
                    if kind == "daiminkan":
                        decisions.append(_finish(pv, pc, prefix, engine))
                    else:
                        pending_call = (pv, pc, prefix)
                elif kind != "ron" or event.get("actor") != seat:
                    decisions.append(_finish(pv, pc, "pass", engine))
                pending = None
            view = replace(view, event=event_no)
            if kind == "dora":
                kan_count = sum(
                    m.kind in {"ankan", "kakan", "daiminkan"} for ms in view.melds for m in ms
                )
                if len(view.indicators) >= kan_count + 1 or len(view.indicators) >= 5:
                    raise RecordError("新宝牌指示牌出现时没有对应杠事件")
                view = replace(view, indicators=view.indicators + (event["tile"],))
                visible_counts(view)
                continue
            actor = event["actor"]
            if kind == "riichi_accept":
                if view.riichi_at[actor] is None or actor in accepted_reaches:
                    raise RecordError("立直成立事件缺少宣言或重复成立")
                scores = list(view.scores)
                if scores[actor] < 1000:
                    raise RecordError("立直供托不足1000")
                scores[actor] -= 1000
                view = replace(view, scores=tuple(scores), kyotaku=view.kyotaku + 1)
                accepted_reaches.add(actor)
                if pending is not None:
                    pending = (view, response_candidates(view))
            elif kind == "draw":
                if need_discard is not None or actor != next_draw or view.live_tiles <= 0:
                    raise RecordError("摸牌顺序/剩余牌数不正确，牌谱可能缺事件")
                if replacement_for is not None and actor != replacement_for:
                    raise RecordError("杠后补牌座位错误")
                hand = view.hand
                drawn = None
                if actor == seat:
                    if event.get("tsumogiri") and event["tile"] != view.drawn:
                        raise RecordError("本人摸切标记与本巡摸牌不符")
                    if event["tile"] == "?":
                        raise RecordError("本人摸牌未知，无法继续计算")
                    hand += (event["tile"],)
                    drawn = event["tile"]
                view = replace(
                    view,
                    hand=hand,
                    drawn=drawn,
                    live_tiles=view.live_tiles - 1,
                    last_discard=None,
                    kuikae=(),
                )
                replacement_for = None
                need_discard = actor
            elif kind in {"discard", "riichi"}:
                if actor != need_discard:
                    raise RecordError("舍牌前缺摸牌/吃碰或座位顺序错误")
                if actor == seat:
                    if pending_call is not None:
                        pv, pc, prefix = pending_call
                        decisions.append(
                            _finish(pv, pc, prefix + f"/discard:{event['tile']}", engine)
                        )
                        pending_call = None
                    else:
                        decisions.append(
                            _finish(view, own_candidates(view), f"{kind}:{event['tile']}", engine)
                        )
                    view = replace(
                        view, hand=_remove(view.hand, (event["tile"],)), drawn=None, kuikae=()
                    )
                elif kind == "riichi" and (
                    view.riichi_at[actor] is not None
                    or view.scores[actor] < 1000
                    or view.live_tiles < 4
                    or any(m.kind != "ankan" for m in view.melds[actor])
                ):
                    raise RecordError("他家立直不满足公开可核验的门清/点数/牌数条件")
                if kind == "riichi":
                    reaches = list(view.riichi_at)
                    reaches[actor] = event_no
                    view = replace(view, riichi_at=tuple(reaches))
                    if event["accepted"]:
                        scores = list(view.scores)
                        scores[actor] -= 1000
                        view = replace(view, scores=tuple(scores), kyotaku=view.kyotaku + 1)
                        accepted_reaches.add(actor)
                rivers = list(view.rivers)
                rivers[actor] += (
                    RiverTile(event["tile"], event_no, tsumogiri=event.get("tsumogiri")),
                )
                view = replace(view, rivers=tuple(rivers), last_discard=(actor, event["tile"]))
                need_discard = None
                next_draw = (actor + 1) % 4
                if actor != seat:
                    candidates = response_candidates(view)
                    if len(candidates) > 1:
                        pending = (view, candidates)
            elif kind in {"chi", "pon", "daiminkan"}:
                taken = event["tile"]
                used = tuple(event["consumed"])
                if (
                    need_discard is not None
                    or view.last_discard != (event["from"], taken)
                    or actor == event["from"]
                    or view.riichi_at[actor] is not None
                ):
                    raise RecordError("鸣牌来源、时机或立直后限制错误")
                ks = sorted(index(t) for t in used + (taken,))
                valid = (
                    len(used) == 2 and len(set(ks)) == 1
                    if kind == "pon"
                    else len(used) == 3 and len(set(ks)) == 1
                    if kind == "daiminkan"
                    else len(used) == 2
                    and ks[0] < 27
                    and ks == list(range(ks[0], ks[0] + 3))
                    and ks[0] // 9 == ks[-1] // 9
                    and event["from"] == (actor - 1) % 4
                )
                if not valid or (kind == "daiminkan" and not kan_available(view)):
                    raise RecordError("吃碰杠组合或上家/杠数/补牌限制错误")
                rivers = list(view.rivers)
                source = event["from"]
                rivers[source] = rivers[source][:-1] + (replace(rivers[source][-1], called=True),)
                melds = list(view.melds)
                melds[actor] += (Meld(kind, used + (taken,)),)
                hand = _remove(view.hand, used) if actor == seat else view.hand
                view = replace(
                    view,
                    rivers=tuple(rivers),
                    melds=tuple(melds),
                    hand=hand,
                    last_discard=None,
                    drawn=None,
                    kuikae=forbidden_after_call(kind, taken, used) if actor == seat else (),
                )
                if kind == "daiminkan":
                    next_draw = actor
                    replacement_for = actor
                else:
                    need_discard = actor
            elif kind in {"ankan", "kakan"}:
                if actor != need_discard or not kan_available(view):
                    raise RecordError("杠时机、补牌或四杠上限错误")
                k = index(event["tile"])
                melds = list(view.melds)
                hand = view.hand
                if actor == seat:
                    decisions.append(
                        _finish(
                            view,
                            own_candidates(view),
                            f"{kind}:{name(k) if kind == 'ankan' else event['tile']}",
                            engine,
                        )
                    )
                if kind == "ankan":
                    used = (
                        tuple(t for t in hand if index(t) == k)
                        if actor == seat
                        else tuple(event.get("consumed", (event["tile"],) * 4))
                    )
                    if actor == seat:
                        hand = _remove(hand, used)
                    melds[actor] += (Meld(kind, used),)
                else:
                    old = next(
                        (m for m in melds[actor] if m.kind == "pon" and index(m.tiles[0]) == k),
                        None,
                    )
                    if old is None or view.riichi_at[actor] is not None:
                        raise RecordError("加杠缺少对应碰牌，或已立直")
                    if actor == seat:
                        hand = _remove(hand, (event["tile"],))
                    melds[actor] = tuple(
                        Meld("kakan", m.tiles + (event["tile"],)) if m is old else m
                        for m in melds[actor]
                    )
                view = replace(view, hand=hand, melds=tuple(melds), last_discard=None, drawn=None)
                need_discard = None
                next_draw = actor
                replacement_for = actor
            elif kind in {"ron", "tsumo"}:
                # Imported adjudication only: baseline lacks full yaku/temp-furiten/win priority.
                ended = True
            visible_counts(view)
            if len(decisions) > 700:
                raise RecordError("决策数量过多，请按局拆分")
        if pending is not None:
            # End-of-fragment does not prove the player passed; do not fabricate an actual action.
            pass
        if pending_call is not None:
            raise RecordError("吃碰后缺少本人舍牌，不能比较完整鸣牌分支")
        if replacement_for is not None and not ended:
            raise RecordError("杠后缺少岭上补牌，牌谱片段无法完成该分支")
        final_hands.append(view.hand)
        final_melds.append(len(view.melds[seat]))
        final_live.append(view.live_tiles)
    return VisibleAnalysis(
        replay.match,
        seat,
        tuple(decisions),
        tuple(final_hands),
        tuple(final_melds),
        tuple(final_live),
        inspected,
        engine.metadata if engine else None,
    )
