"""Freeze visible-only decisions first; independent postgame verification comes second."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from app.services.mahjong.analysis import (
    Decision,
    LocalStrategyEngine,
    VisibleAnalysis,
    analyze_visible,
    ranking,
)
from app.services.mahjong.coach import coach_request, load_coach_prompt
from app.services.mahjong.knowledge import KnowledgeSource, load_sources, retrieve_passages
from app.services.mahjong.records import RecordError, Replay, decision_input, seat_number
from app.services.mahjong.tiles import shanten, tile, waits


@dataclass(frozen=True)
class Review:
    analysis: VisibleAnalysis
    warnings: tuple[str, ...]
    postgame: tuple[str, ...]
    sources: tuple[KnowledgeSource, ...] = ()
    coach_prompt_sha256: str | None = None
    coach_context: tuple[dict[str, str], ...] = ()


def noten_payments(tenpai: tuple[bool, ...], pool: int = 3000) -> tuple[int, ...]:
    """Exhaustive-draw noten transfer ONLY; never chombo/riichi deposit/nagashi."""
    if len(tenpai) != 4 or any(type(v) is not bool for v in tenpai):
        raise ValueError("四家听牌状态必须是布尔值")
    if type(pool) is not int or pool < 0 or pool % 6:
        raise ValueError("罚符总额须可按1/2/3人精确分配")
    n = sum(tenpai)
    if n in (0, 4):
        return (0, 0, 0, 0)
    return tuple(pool // n if ready else -(pool // (4 - n)) for ready in tenpai)


def _postgame(replay: Replay, analysis: VisibleAnalysis) -> tuple[str, ...]:
    result = []
    for number, raw in enumerate(replay.rounds):
        outcome = raw["result"]
        prefix = f"第{number + 1}局："
        if not outcome:
            result.append(prefix + "无终局结果，不补写隐藏手牌/实际输赢。")
            continue
        kind = outcome["type"]
        if kind == "exhaustive_draw":
            if analysis.final_live_tiles[number] != 0:
                result.append(
                    prefix + "导出标注荒牌流局，但已提供事件尚未耗尽牌山；时间线不完整。"
                    "下文仅按导出声明作条件重算，不代表实际结算已核验。"
                )
            ready = outcome.get("tenpai")
            if (
                not isinstance(ready, list)
                or len(ready) != 4
                or any(type(v) is not bool for v in ready)
            ):
                result.append(
                    prefix + "荒牌流局，但缺少四家经平台认定的听牌状态；无法核算实际罚符。"
                )
            else:
                payment = noten_payments(tuple(ready))
                result.append(
                    prefix + f"荒牌流局，导出声明听牌{tuple(ready)}；按所选标准四麻子集R=3000，"
                    f"未听罚符四家{payment}，合计{sum(payment)}。该项是点数转移，不是违规处罚。"
                )
                hand = analysis.final_hands[number]
                n = analysis.final_meld_counts[number]
                if len(hand) == 13 - 3 * n:
                    structural = shanten(hand, n) == 0 and bool(waits(hand, n))
                    result.append(
                        prefix
                        + f"独立核对本人终局形状听牌={structural}；"
                        + (
                            "与导出声明一致。"
                            if structural == ready[analysis.seat]
                            else "与导出声明不一致，平台听牌认定/牌谱完整性需核实。"
                        )
                    )
                deltas = outcome.get("deltas")
                if deltas is not None:
                    if (
                        not isinstance(deltas, list)
                        or any(type(v) is not int for v in deltas)
                        or len(deltas) != 4
                    ):
                        result.append(prefix + "终局分差字段无效，未据此评分。")
                    elif tuple(deltas) != payment:
                        result.append(
                            prefix
                            + "导出分差与单项未听罚符不符；可能有遗漏或额外结算，未强行合并。"
                        )
                    else:
                        result.append(prefix + "导出四家分差与该项罚符一致。")
            result.append(
                prefix + "供托保留/本场与亲家连庄/终局条件须另核对；不能重复加入未听罚符。"
            )
        elif kind == "draw_unknown":
            result.append(
                prefix + "导出仅记录流局，未说明荒牌/途中/流局满贯；不能从分差猜罚符或听牌人数。"
            )
        elif kind == "abortive_draw":
            result.append(
                prefix + "途中流局，不应用荒牌流局的3000点未听罚符；具体原因、供托和本场另核对。"
            )
        elif kind == "nagashi_mangan":
            result.append(
                prefix + "流局满贯，不能按未听罚符结算；多家成立/本场/供托尚未实现自动计分。"
            )
        else:
            result.append(
                prefix + f"导出终局为{'荣和' if kind == 'ron' else '自摸'}；"
                "未实现完整番符、和牌优先级、供托与本场结算，不能声称分数已核验。"
            )
        revealed = outcome.get("revealed_hands")
        if revealed is not None:
            if not isinstance(revealed, list) or len(revealed) != 4:
                result.append(prefix + "事后公开暗手字段格式错误，未采用。")
                continue
            for actor, hand in enumerate(revealed):
                if hand is None:
                    continue
                if not isinstance(hand, list) or len(hand) > 14:
                    result.append(prefix + f"座位{actor}事后暗手格式不明。")
                    continue
                try:
                    tiles = tuple(tile(t) for t in hand)
                    result.append(
                        prefix + f"事后公开座位{actor}暗手 {' '.join(tiles)}。"
                        "此信息在决策阶段不可用，未回写候选/建议。"
                    )
                except ValueError:
                    result.append(prefix + f"座位{actor}事后暗手编码不明。")
    return tuple(result)


def review(replay: Replay, seat: str | int, *, engine: LocalStrategyEngine | None = None) -> Review:
    selected = seat_number(seat)
    masked = decision_input(replay, selected)
    # No all-seeing Replay is passed to analyze_visible or the optional engine.
    frozen = analyze_visible(masked, engine=engine)
    # Frozen dataclasses/tuples cannot be amended by the separate postgame renderer.
    postgame = _postgame(replay, frozen)
    try:
        sources = load_sources(Path(__file__).resolve().parents[3])
        warnings = replay.warnings
    except RecordError as exc:
        sources = ()
        warnings = replay.warnings + ("本地知识库未使用：" + str(exc),)
    coach_hash = None
    coach_context = ()
    try:
        prompt = load_coach_prompt()
        coach_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if frozen.decisions:
            coach_context = coach_request(
                "保留原机器人角色语气；本次仅切换到严谨的牌谱教学任务。", frozen.decisions[0]
            )
    except (RecordError, OSError) as exc:
        coach_hash = None
        sources = ()
        warnings += ("教练/教材MD读取失败，已降级为纯规则计算，未调用教练知识：" + str(exc),)
    return Review(frozen, warnings, postgame, sources, coach_hash, coach_context)


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def decision_markdown(
    decision: Decision,
    number: int,
    *,
    start: int = 0,
    limit: int | None = None,
    sources: tuple[KnowledgeSource, ...] = (),
) -> str:
    view = decision.view
    actual = next(c for c in decision.candidates if c.id == decision.actual)
    own_melds = view.melds[view.seat]
    lines = [
        f"### 节点{number}｜{view.round // 4 + 1}场{view.round % 4 + 1}局／事件{view.event}／本人第{view.turn}次出牌前",
        f"定位：{view.phase}（巡目按本人舍牌计数；牌山剩余{view.live_tiles}枚，阶段分箱仅作描述）。",
        (
            f"本人开局固定座位{view.seat}；当前{'庄家' if view.seat == view.dealer else '子家'}；"
            f"手牌 {' '.join(view.hand)}；已有副露/暗杠{len(own_melds)}组。"
        ),
        f"实际动作：**{actual.action}**；关键原因：{'；'.join(decision.key_reasons) or '普通牌效/退路复核'}。",
        "[确定计算/规则条件] " + ranking(view),
        "目标：综合整场顺位；领先可重视避险，落后须核对追分需求。未实现完整顺位效用模型。",
        "| 行动 | 合法性 | 速度/形状进张 | 价值 | 安全与退路 | 优点 | 代价 |",
        "|---|---|---|---|---|---|---|",
    ]
    chosen = (
        decision.candidates[start:] if limit is None else decision.candidates[start : start + limit]
    )
    for candidate in chosen:
        outs = ", ".join(f"{t}×{n}" for t, n in candidate.ukeire) or "无可见形状进张/待补牌后计算"
        speed = f"向听{candidate.shanten}；{outs}，合计{sum(n for _, n in candidate.ukeire)}未见枚"
        if candidate.shanten is None:
            speed = "补牌及合法舍牌后形状未知，不伪造该分支进张"
        values = (
            candidate.action + ("【实际】" if candidate.id == decision.actual else ""),
            candidate.legality,
            speed,
            candidate.value,
            candidate.safety,
            candidate.benefit,
            candidate.cost,
        )
        lines.append("| " + " | ".join(_cell(v) for v in values) + " |")
    lines.extend(
        [
            (
                "进张仅为标准形/七对子/国士的降向听牌与4减已知枚数；不是牌山真余枚，不是概率。"
                "形状和牌还要满足役、舍牌/临时/立直振听等限制；本版未提供完整荣和/自摸合法动作裁定。"
            ),
            "[教材解释/不确定推断] " + decision.advice,
            (
                "[条件情景] 晚巡若最终1/2/3家听牌，听牌者分别收3000/1500/1000，"
                "未听者分别付1000/1500/3000；0或4家该项为0。"
                "其他三家当时听牌未知，不能偷看暗手决定押牌；仍需同时考虑他家和牌与亲家连庄。"
            ),
            (
                "转折条件：新增立直/副露、剩余摸牌减少、摸入安全牌或关键进张、役/宝牌改变时重新评估；"
                "早巡也须回应明确威胁，晚巡也不能无条件强行保听。"
            ),
            "评价：**证据不足/合理取舍待复核**；计算结果可复算，策略判断无校准置信度，不能报告错误概率。",
        ]
    )
    for estimate in decision.engine:
        lines.append(f"[引擎估计，非概率] {estimate.candidate_id}: {_cell(estimate.explanation)}")
    if sources:
        for passage in retrieve_passages(
            Path(__file__).resolve().parents[3], view, decision.candidates, sources
        ):
            lines.append(
                f"[教材解释] {passage.topic}（资料{passage.source_id}）：{passage.text}。"
                f"适用与例外：{passage.applicability}。这是本地文字教学，不是当前暗牌证据。"
            )
    return "\n\n".join(lines[:6]) + "\n\n" + "\n".join(lines[6:])


BOUNDARY = (
    "规则配置：majsoul-standard-4p 的保守子集；四麻136牌、常规赤五、标准门清立直门槛、"
    "标准吃碰/食替与杠检查、荒牌流局R=3000。自定义房间不支持。"
    "未完成全役番符/临时振听/和牌优先级/抢杠裁定、四杠终止时机、连庄止/延长/飞人/同分顺序；"
    "仅称通过已实现规则检查，不宣称穷举全部合法动作或平台规则认证。"
    "未执行神经网络训练，未安装策略权重，未调用LLM/第三方牌谱服务，未给出任何和牌/放铳概率。"
)


def markdown(result: Review) -> str:
    analysis = result.analysis
    key = [i for i, d in enumerate(analysis.decisions, 1) if d.key_reasons]
    lines = [
        "# 雀魂四人麻将赛后复盘（透明规则基线）",
        "## 复盘结论",
        (
            f"检查{analysis.inspected_events}条已提供事件，记录{len(analysis.decisions)}个本人决策节点；"
            f"关键节点{', '.join(map(str, key)) or '暂无'}。每次已提供的本人舍牌/杠与可鸣机会均列在下文。"
        ),
        "可执行原则：先核对向听/进张，再分别检验役与打点、对每家的安全性、顺位需求；不把形状排序当最优策略。",
        "## 规则、资料与分析边界",
        BOUNDARY,
        f"赛制{'东风' if analysis.match == 'tonpuu' else '半庄'}；本人开局固定座位{analysis.seat}（0东1南2西3北）。",
        (
            "信息隔离：已通过代码边界。决策模块输入为VisibleReplay，逐事件创建不可变DecisionView；"
            "没有对手暗手、牌山组成、后续结果或未来宝牌。决策输出冻结后，独立核对终局。"
        ),
        "巡目按本人已舍牌次数+1统计；早≤6、中7–12、晚≥13或牌山≤16仅作描述，不能单独判断安全。",
    ]
    lines.extend(result.warnings)
    lines.append(
        "专业教练MD与本地主题教材："
        + (
            f"已读取并构建隔离上下文；提示词SHA256 {result.coach_prompt_sha256}。当前使用规则基线，没有调用外部AI。"
            if result.coach_prompt_sha256
            else "未加载成功，当前仅有形状规则计算，不能声称已调用教练知识。"
        )
    )
    if analysis.engine_metadata:
        lines.append("可选本地引擎元数据（未声称校准）：" + _cell(asdict(analysis.engine_metadata)))
    else:
        lines.append(
            "引擎估计：本次没有使用策略引擎。确定计算：tiles.py向听/形状进张与公开牌去重。"
        )
    lines.append("## 关键节点与逐巡检查｜仅当时可见信息")
    for number, decision in enumerate(analysis.decisions, 1):
        lines.append(decision_markdown(decision, number, sources=result.sources))
    lines.extend(
        [
            "## 流局与结算检查／事后全知解释｜不参与决策评分",
            *result.postgame,
            "## 下次训练清单",
            "1. 遇到立直：分别指出每家现物与未知风险，比较同向听进攻牌与可保留的安牌。",
            "2. 能杠时：逐项检查合法性、形状/听牌、四家新宝牌、抢杠与不杠退路，再做选择。",
            "3. 晚巡：列0–4家听牌条件收益，并说明押牌和弃听各自代价；不能用事后暗牌给当时决策喂答案。",
            "## 来源与待核实项",
            (
                "本次计算来源：本仓库开源形状规则实现与测试；内部基础教学在docs/mahjong/LOCAL_GUIDE.md。"
                "雀魂官方基础说明：https://mahjongsoul.yo-star.com/faq （本次网页正文读取不足，仅作待核对入口；"
                "不能宣称已核实所有房间细则）。知识导入接口可保存来源、时间戳、内容哈希与核验状态；"
                "知识资料仅作条件化本地文字检索，未声称训练完成或获得策略模型。"
            ),
        ]
    )
    for source in result.sources:
        lines.append(
            f"- 资料{source.id}：{source.title}；{source.file}；SHA256 {source.sha256}；"
            f"来源 {source.source}；核验 {source.verified_by}；覆盖 {', '.join(source.timecodes) or '见正文'}。"
        )
    lines.append(
        "《长期意识》依用户要求暂缓，未作为策略依据。来源附录用于追溯，理解正文无需联网或观看视频。"
    )
    return "\n\n".join(lines) + "\n"


def summary(result: Review, job_id: str) -> str:
    a = result.analysis
    keys = [str(i) for i, d in enumerate(a.decisions, 1) if d.key_reasons]
    return (
        f"雀魂{'东风' if a.match == 'tonpuu' else '半庄'}复盘 {job_id}｜本人开局座位{a.seat}\n"
        f"已查{a.inspected_events}事件/{len(a.decisions)}决策；关键节点：{', '.join(keys[:12]) or '暂无'}"
        + ("…" if len(keys) > 12 else "")
        + "\n"
        "方法：向听/可见形状进张的规则基线；无策略权重，不输出胜率或放铳率。决策与事后暗牌已隔离。\n"
        + (
            "专业教练MD和本地主题教材已读取；无外部AI调用。\n"
            if result.coach_prompt_sha256
            else "教练/教材MD未加载，已降级为纯规则计算。\n"
        )
        + f"展开：/牌谱分析 详情 {job_id} 1；分页追加页码；导出：/牌谱分析 导出 {job_id}\n"
        "牌谱为片段时仅分析片段；完整计分与全部平台细则尚未实现。"
    )


def brief_decision(result: Review, number: int, page: int) -> str:
    """Compact QQ page; full candidate causality stays intact in the local report."""
    decision = result.analysis.decisions[number - 1]
    view = decision.view
    chosen = next(c for c in decision.candidates if c.id == decision.actual)
    pages = (len(decision.candidates) + 1) // 2
    lines = [
        (
            f"节点{number}｜{'东南西北'[view.round // 4]}{view.round % 4 + 1}局／事件{view.event}／"
            f"本人第{view.turn}次出牌前／{view.phase}／公开剩余{view.live_tiles}枚"
        ),
        f"实际：{chosen.action}；第{page}/{pages}页。",
        f"本人手牌：{' '.join(view.hand)}；当前四家点数{view.scores}。",
    ]
    for candidate in decision.candidates[(page - 1) * 2 : page * 2]:
        outs = " ".join(f"{t}×{n}" for t, n in candidate.ukeire) or "无已算出的降向听进张"
        lines.extend(
            [
                f"【{'实际' if candidate.id == decision.actual else '候选'}】{candidate.action}",
                f"计算：向听{candidate.shanten}，未见进张合计{sum(n for _, n in candidate.ukeire)}；{outs}",
                f"价值：{candidate.value[:240]}",
                f"安全：{candidate.safety[:240]}",
                f"优点：{candidate.benefit[:180]}",
                f"代价：{candidate.cost[:240]}",
            ]
        )
    if result.sources:
        passage = retrieve_passages(
            Path(__file__).resolve().parents[3], view, decision.candidates, result.sources
        )[0]
        lines.append(f"本地教材｜{passage.topic}：{passage.text[:350]}")
    else:
        lines.append("本地教练知识未加载，此处仅有规则基线计算。")
    lines.extend(
        [
            "未见枚数不是概率；形状待牌还须核验役与振听。顺位取舍须结合本场供托、直击/自摸及终局条件。",
            "完整候选、流局条件收益、转折条件与事后验证见本地导出；本页是短摘要，无外部AI调用。",
        ]
    )
    return "\n".join(lines)
