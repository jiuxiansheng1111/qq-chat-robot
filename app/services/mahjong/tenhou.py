"""Local tenhou.net/6 JSON -> standard mjai events, with bounded ambiguity search.

Format reference: Equim-chan/mjai-reviewer convlog (Apache-2.0). This independent
adapter does not fetch logs, use credentials, execute an external converter, or
claim that a Tenhou fixture is a Mahjong Soul end-to-end test.
"""

from __future__ import annotations

import re

from app.services.mahjong.records import MAX_EVENTS, RecordError


def _tile(value: object) -> str:
    if type(value) is not int:
        raise RecordError("tenhou.net/6牌编码必须为整数")
    if value in (51, 52, 53):
        return ("5mr", "5pr", "5sr")[value - 51]
    suit, rank = divmod(value, 10)
    if suit in (1, 2, 3) and 1 <= rank <= 9:
        return f"{rank}{'mps'[suit - 1]}"
    if suit == 4 and 1 <= rank <= 7:
        return ("E", "S", "W", "N", "P", "F", "C")[rank - 1]
    raise RecordError("tenhou.net/6含未知牌编码")


def _naki(actor: int, value: object) -> dict:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9cp mak]+", value.replace(" ", "")):
        raise RecordError("tenhou.net/6副露字符串格式错误")
    markers = [(i, c) for i, c in enumerate(value) if not c.isdecimal()]
    if len(markers) != 1:
        raise RecordError("tenhou.net/6副露操作符不唯一")
    position, marker = markers[0]
    digits = value[:position] + value[position + 1 :]
    if len(digits) not in (6, 8) or position % 2:
        raise RecordError("tenhou.net/6副露牌数错误")
    tiles = [_tile(int(digits[i : i + 2])) for i in range(0, len(digits), 2)]
    at = position // 2
    if marker == "a" and len(tiles) == 4 and at == 3:
        return {"type": "ankan", "actor": actor, "consumed": tiles}
    if marker == "k" and len(tiles) == 4 and at in (0, 1, 2):
        return {
            "type": "kakan",
            "actor": actor,
            "pai": tiles[at],
            "consumed": [t for n, t in enumerate(tiles) if n != at],
        }
    expected = 3 if marker in ("c", "p") else 4 if marker == "m" else 0
    positions = {"c": {0: 3}, "p": {0: 3, 1: 2, 2: 1}, "m": {0: 3, 1: 2, 3: 1}}
    if len(tiles) != expected or at not in positions.get(marker, {}):
        raise RecordError("tenhou.net/6鸣牌位置编码错误")
    return {
        "type": {"c": "chi", "p": "pon", "m": "daiminkan"}[marker],
        "actor": actor,
        "target": (actor + positions[marker][at]) % 4,
        "pai": tiles[at],
        "consumed": [t for n, t in enumerate(tiles) if n != at],
    }


def _deltas(value: object) -> list[int]:
    if not isinstance(value, list) or len(value) != 4 or any(type(v) is not int for v in value):
        raise RecordError("tenhou.net/6终局分差错误")
    return value


def _end(raw: object) -> tuple[list[dict], set[tuple[int, int]]]:
    if not isinstance(raw, list) or not raw:
        raise RecordError("tenhou.net/6缺少终局信息")
    if raw[0] == "和了":
        if len(raw) < 3 or len(raw) % 2 != 1:
            raise RecordError("tenhou.net/6和牌结算结构错误")
        result = []
        winners = set()
        for k in range(1, len(raw), 2):
            delta = _deltas(raw[k])
            detail = raw[k + 1]
            if (
                not isinstance(detail, list)
                or len(detail) < 2
                or any(type(v) is not int or not 0 <= v <= 3 for v in detail[:2])
            ):
                raise RecordError("tenhou.net/6和牌座位错误")
            who, target = detail[:2]
            result.append({"type": "hora", "actor": who, "target": target, "deltas": delta})
            winners.add((who, target))
        return result, winners
    reasons = {
        "流局": None,
        "九種九牌": "kyushukyuhai",
        "四家立直": "suchareach",
        "四開槓": "sukaikan",
        "四風連打": "sufonrenta",
        "三家和": "sanchaho",
    }
    if raw[0] not in reasons:
        raise RecordError("tenhou.net/6终局类型未支持，不能猜测流局满贯/处罚")
    event = {"type": "ryukyoku"}
    if reasons[raw[0]]:
        event["reason"] = reasons[raw[0]]
    if len(raw) > 1:
        event["deltas"] = _deltas(raw[1])
    return [event], set()


def _round(raw: object) -> list[dict]:
    if not isinstance(raw, list) or len(raw) != 17:
        raise RecordError("tenhou.net/6每局必须有17项（仅四麻）")
    meta, scores, indicators = raw[:3]
    if (
        not isinstance(meta, list)
        or len(meta) != 3
        or any(type(v) is not int or v < 0 for v in meta)
        or meta[0] > 15
    ):
        raise RecordError("tenhou.net/6局号、本场、供托错误")
    if not isinstance(indicators, list) or not 1 <= len(indicators) <= 5:
        raise RecordError("tenhou.net/6宝牌指示牌数量错误")
    indicators = [_tile(n) for n in indicators]
    hands, takes, discards = [], [], []
    for actor in range(4):
        h, take, discard = raw[4 + actor * 3 : 7 + actor * 3]
        if (
            not isinstance(h, list)
            or len(h) != 13
            or not isinstance(take, list)
            or not isinstance(discard, list)
        ):
            raise RecordError("tenhou.net/6起手/摸打数组错误")
        if len(take) + len(discard) > 200:
            raise RecordError("tenhou.net/6单家事件过多")
        hands.append([_tile(n) for n in h])
        takes.append(
            [
                _naki(actor, n)
                if isinstance(n, str)
                else {"type": "tsumo", "actor": actor, "pai": _tile(n)}
                for n in take
            ]
        )
        discards.append(discard)
    end, winners = _end(raw[16])
    initial = {
        "type": "start_kyoku",
        "bakaze": "ESWN"[meta[0] // 4],
        "kyoku": meta[0] % 4 + 1,
        "oya": meta[0] % 4,
        "honba": meta[1],
        "kyotaku": meta[2],
        "scores": scores,
        "tehais": hands,
        "dora_marker": indicators[0],
    }
    solutions: list[list[dict]] = []
    explored = 0

    def walk(
        actor: int,
        ti: tuple[int, ...],
        di: tuple[int, ...],
        events: list[dict],
        dora_n: int,
        pending_reach: int | None,
        delayed_dora: bool = False,
    ) -> None:
        nonlocal explored
        explored += 1
        if explored > 5000 or len(solutions) > 1:
            return
        if len(events) > 400:
            return
        if all(ti[a] == len(takes[a]) and di[a] == len(discards[a]) for a in range(4)):
            final = events.copy()
            if pending_reach is not None and not winners:
                final.append({"type": "reach_accepted", "actor": pending_reach})
            if dora_n != len(indicators):
                return
            solutions.append([initial] + final + end + [{"type": "end_kyoku"}])
            return
        if ti[actor] >= len(takes[actor]):
            return
        take = takes[actor][ti[actor]]
        previous = next(
            (
                e
                for e in reversed(events)
                if e["type"] in {"dahai", "tsumo", "chi", "pon", "daiminkan", "ankan", "kakan"}
            ),
            None,
        )
        if take["type"] != "tsumo" and (
            previous is None
            or previous["type"] != "dahai"
            or previous["actor"] != take["target"]
            or previous["pai"] != take["pai"]
        ):
            return
        new_ti, new_di = list(ti), list(di)
        new_ti[actor] += 1
        now = events.copy()
        if pending_reach is not None:
            now.append({"type": "reach_accepted", "actor": pending_reach})
            pending_reach = None
        if delayed_dora:
            if dora_n >= len(indicators):
                return
            now.append({"type": "dora", "dora_marker": indicators[dora_n]})
            dora_n += 1
        now.append(take)
        if take["type"] == "daiminkan":
            # Tenhou encodes a zero placeholder as the corresponding 'discard'.
            if new_di[actor] >= len(discards[actor]) or discards[actor][new_di[actor]] != 0:
                return
            new_di[actor] += 1
            walk(actor, tuple(new_ti), tuple(new_di), now, dora_n, None, True)
            return
        if new_di[actor] >= len(discards[actor]):
            if (actor, actor) in winners:
                walk(actor, tuple(new_ti), tuple(new_di), now, dora_n, None)
            return
        discard = discards[actor][new_di[actor]]
        new_di[actor] += 1
        if isinstance(discard, str) and any(c in discard for c in "ak"):
            kan = _naki(actor, discard)
            if kan["type"] not in {"ankan", "kakan"}:
                return
            now.append(kan)
            robbed = any(who != actor and target == actor for who, target in winners)
            # A chankan can terminate here; no replacement draw / future dora is exposed.
            if robbed and all(
                new_ti[a] == len(takes[a]) and new_di[a] == len(discards[a]) for a in range(4)
            ):
                walk(actor, tuple(new_ti), tuple(new_di), now, dora_n, None)
                return
            walk(actor, tuple(new_ti), tuple(new_di), now, dora_n, None, True)
            return
        reached = isinstance(discard, str) and discard.startswith("r")
        if reached:
            if not re.fullmatch(r"r\d{2}", discard):
                return
            discard = int(discard[1:])
            now.append({"type": "reach", "actor": actor})
            pending_reach = actor
        tsumogiri = discard == 60
        if tsumogiri and take["type"] != "tsumo":
            return
        pai = take["pai"] if tsumogiri else _tile(discard)
        now.append({"type": "dahai", "actor": actor, "pai": pai, "tsumogiri": tsumogiri})
        calls = []
        for caller in range(4):
            if caller != actor and new_ti[caller] < len(takes[caller]):
                candidate = takes[caller][new_ti[caller]]
                if (
                    candidate["type"] in {"chi", "pon", "daiminkan"}
                    and candidate["target"] == actor
                    and candidate["pai"] == pai
                ):
                    calls.append(caller)
        if not calls:
            walk((actor + 1) % 4, tuple(new_ti), tuple(new_di), now, dora_n, pending_reach)
        else:
            for caller in calls:
                walk(caller, tuple(new_ti), tuple(new_di), now, dora_n, pending_reach)
            # Repeated identical discards can make the upcoming call belong to a later turn.
            if any(
                (d == discard or (isinstance(d, str) and d == f"r{discard}"))
                for d in discards[actor][new_di[actor] :]
            ):
                walk((actor + 1) % 4, tuple(new_ti), tuple(new_di), now, dora_n, pending_reach)

    walk(meta[0] % 4, (0,) * 4, (0,) * 4, [], 1, None)
    if len(solutions) != 1:
        raise RecordError(
            "tenhou.net/6事件顺序无法唯一重建或超出支持子集；请改用标准mjai导出，未猜补时间线"
        )
    return solutions[0]


def to_mjai(data: dict, match: str | None) -> list[dict]:
    if match is None:
        raise RecordError("tenhou兼容导出需明确选择东风/半庄")
    names = data.get("name")
    rules = data.get("rule")
    rounds = data.get("log")
    if (
        not isinstance(names, list)
        or len(names) != 4
        or not isinstance(rules, dict)
        or not isinstance(rounds, list)
        or not 1 <= len(rounds) <= 32
    ):
        raise RecordError("tenhou.net/6四家/规则/局数组缺失")
    display = str(rules.get("disp", ""))
    if "三" in display or "sanma" in display.lower():
        raise RecordError("不能用三麻导出按四麻分析")
    if ("南" in display and match != "hanchan") or (
        ("東" in display or "东" in display) and match != "tonpuu"
    ):
        raise RecordError("所选东风/半庄与tenhou兼容导出的规则显示不符")
    events = [{"type": "start_game", "names": ["A", "B", "C", "D"]}]
    try:
        for raw in rounds:
            events.extend(_round(raw))
            if len(events) > MAX_EVENTS + 100:
                raise RecordError("tenhou兼容导出事件过多，请按局拆分")
    except RecursionError as exc:
        raise RecordError("tenhou兼容导出时间线过深，未继续猜测重建") from exc
    events.append({"type": "end_game"})
    return events
