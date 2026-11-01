"""Convert complete official Record events, retaining round results outside decision input."""

from __future__ import annotations

import json
from dataclasses import replace
from itertools import pairwise

from app.services.mahjong.records import FORMAT, RULES, RecordError, parse_record, seat_number
from app.services.mahjong.tiles import index, tile


def convert_game(head: dict, records: list[dict], target_id: int | None, seat=None, match=None):
    mode = head.get("config", {}).get("mode", {})
    players = head.get("result", {}).get("players", [])
    if len(players) != 4 or mode.get("mode") not in (1, 2):
        raise RecordError("目前只支持雀魂普通四人东风/半庄，不将三麻或活动规则按四麻误算")
    # Friend-room rule variations must be checked before claiming standard four-player analysis.
    if head.get("config", {}).get("category") != 2:
        raise RecordError("这份牌谱不是标准匹配四麻；友人房/赛事自定义规则尚需核验，不能冒充完整复盘")
    actual_match = "tonpuu" if mode["mode"] == 1 else "hanchan"
    if seat is None:
        matches = [account["seat"] for account in head.get("accounts", [])
                   if account.get("account_id") == target_id and target_id is not None]
        if len(matches) != 1:
            raise RecordError("已读取整场牌谱，但分享链接没有明确玩家视角。请在原链接后补 座位=东/南/西/北（开局固定座位）")
        seat = matches[0]
    seat = seat_number(seat)
    rounds, current, hands = [], None, None
    indicators, accepted = [], set()
    last_discard = None

    def remove(actor, tiles):
        try:
            for value in tiles:
                hands[actor].remove(value)
        except (ValueError, IndexError) as exc:
            raise RecordError("原始牌谱摸打手牌不一致，未跳过缺失事件") from exc

    def doras(data):
        nonlocal indicators
        new = data.get("doras", [])
        if not new:
            return
        new = [tile(value) for value in new]
        if new[:len(indicators)] != indicators:
            raise RecordError("宝牌指示牌记录不连续")
        for value in new[len(indicators):]:
            current["events"].append({"type": "dora", "tile": value})
        indicators = new

    for record in records:
        kind, data = record["name"], record["data"]
        if kind == "RecordNewRound":
            if current is not None:
                raise RecordError("上一局缺少结算，不能当成完整牌谱")
            dealer = data["ju"]
            if dealer not in range(4) or len(data["scores"]) != 4:
                raise RecordError("四人对局座位或点数不完整")
            hands = [[tile(t) for t in data.get("tiles" + str(i), [])] for i in range(4)]
            if any(len(hand) != (14 if i == dealer else 13) for i, hand in enumerate(hands)):
                raise RecordError("四家起手不完整，不能只凭截图或片段分析整场")
            initial = [hand.copy() for hand in hands]
            first_draw = initial[dealer].pop()
            indicators = [tile(t) for t in (data.get("doras") or [data.get("dora")])]
            if len(indicators) != 1:
                raise RecordError("开局宝牌记录不完整")
            current = {"round": data["chang"] * 4 + dealer, "dealer": dealer, "scores": data["scores"],
                       "hands": initial, "dora_indicators": indicators.copy(), "honba": data["ben"],
                       "kyotaku": data["liqibang"], "events": [{"type": "draw", "actor": dealer, "tile": first_draw}]}
            last_discard = None
            accepted = set()
            continue
        if current is None:
            raise RecordError("局外出现摸打记录，牌谱顺序不完整")
        success = data.get("liqi")
        if success and not success.get("failed", False):
            actor = success["seat"]
            if actor in accepted:
                raise RecordError("立直成立记录重复")
            accepted.add(actor)
            current["events"].append({"type": "riichi_accept", "actor": actor})
        if kind == "RecordDealTile":
            doras(data)
            actor, value = data["seat"], tile(data["tile"])
            hands[actor].append(value)
            current["events"].append({"type": "draw", "actor": actor, "tile": value})
        elif kind == "RecordDiscardTile":
            doras(data)
            actor, value = data["seat"], tile(data["tile"])
            remove(actor, [value])
            event = {"type": "riichi" if data.get("is_liqi") or data.get("is_wliqi") else "discard",
                     "actor": actor, "tile": value, "tsumogiri": data.get("moqie", False)}
            if event["type"] == "riichi":
                event["accepted"] = False
            current["events"].append(event)
            last_discard = (actor, value)
        elif kind == "RecordChiPengGang":
            actor, call = data["seat"], data["type"]
            values = [tile(value) for value in data["tiles"]]
            if (call not in (0, 1, 2) or last_discard is None
                    or len(values) != (4 if call == 2 else 3)):
                raise RecordError("吃碰杠数据或来源不完整")
            source, taken = last_discard
            froms = data.get("froms", [])
            if froms:
                indices = [i for i, owner in enumerate(froms) if owner != actor]
                if len(froms) != len(values) or len(indices) != 1 or froms[indices[0]] != source:
                    raise RecordError("鸣牌来源与最近舍牌不一致")
                taken_index = indices[0]
            else:
                taken_index = len(values) - 1
            if values[taken_index] != taken:
                raise RecordError("鸣牌牌张与最近舍牌不一致")
            consumed = [value for i, value in enumerate(values) if i != taken_index]
            remove(actor, consumed)
            current["events"].append({"type": ("chi", "pon", "daiminkan")[call], "actor": actor,
                                      "tile": taken, "from": source, "consumed": consumed})
            last_discard = None
        elif kind == "RecordAnGangAddGang":
            actor, value = data["seat"], tile(data["tiles"])
            if data["type"] == 3:
                consumed = [t for t in hands[actor] if index(t) == index(value)]
                if len(consumed) != 4:
                    raise RecordError("暗杠缺少完整四枚手牌")
                remove(actor, consumed)
                event = {"type": "ankan", "actor": actor, "tile": value, "consumed": consumed}
            elif data["type"] == 2:
                remove(actor, [value])
                event = {"type": "kakan", "actor": actor, "tile": value}
            else:
                raise RecordError("未支持的杠类型")
            current["events"].append(event)
            last_discard = (actor, value)  # A following ron may be robbing this added kan.
            doras(data)
        elif kind in {"RecordHule", "RecordNoTile", "RecordLiuJu"}:
            running = [score - (1000 if i in accepted else 0) for i, score in enumerate(current["scores"])]
            if kind == "RecordHule":
                wins = data.get("hules", [])
                if not wins or len(data.get("scores", [])) != 4:
                    raise RecordError("和牌与四家结算缺失")
                result_type = "tsumo" if wins[0]["zimo"] else "ron"
                for win in wins:
                    current["events"].append({"type": "tsumo" if win["zimo"] else "ron", "actor": win["seat"]})
                result = {"type": result_type, "scores": data["scores"], "winners": [w["seat"] for w in wins]}
                if result_type == "ron" and last_discard is not None:
                    result["from"] = last_discard[0]
            elif kind == "RecordNoTile":
                deltas = [0] * 4
                for item in data.get("scores", []):
                    delta = item.get("delta_scores", [])
                    if len(delta) != 4:
                        raise RecordError("流局结算记录不完整")
                    deltas = [a + b for a, b in zip(deltas, delta, strict=True)]
                result = {"type": "nagashi_mangan" if data.get("liujumanguan") else "exhaustive_draw",
                          "scores": [a + b for a, b in zip(running, deltas, strict=True)], "deltas": deltas}
                if len(data.get("players", [])) == 4:
                    result["tenpai"] = [bool(p["tingpai"]) for p in data["players"]]
            else:
                result = {"type": "abortive_draw", "scores": data.get("gameend", {}).get("scores") or running}
            if len(result["scores"]) != 4:
                raise RecordError("四家结算点数不完整")
            current["result"] = result
            rounds.append(current)
            current = None
        else:
            raise RecordError("存在未实现的特殊对局事件，未删掉事件继续分析")
    if current is not None or not rounds:
        raise RecordError("缺少最后一局的结算，不能声称整场分析完成")
    if rounds[0]["round"] != 0 or rounds[0]["honba"] != 0:
        raise RecordError("牌谱没有从东一局开始，暂不能作为完整对局导入")
    for before, after in pairwise(rounds):
        if before["result"]["scores"] != after["scores"]:
            raise RecordError("前后局点数不连续，可能有缺失的立直或结算记录")
    replay = parse_record(json.dumps({"format": FORMAT, "rules": RULES, "players": 4,
                                      "match": actual_match, "rounds": rounds}).encode(), match=match)
    replay = replace(replay, source="majsoul-official-rpc")
    return replay, seat


def whole_game_overview(replay, seat: int) -> tuple[str, ...]:
    first, last = replay.rounds[0], replay.rounds[-1]
    final = last["result"]["scores"]
    rank = 1 + sum(score > final[seat] for score in final)
    lines = [(f"整场共{len(replay.rounds)}局（含连庄），本人开局座位{'东南西北'[seat]}；"
              f"点数{first['scores'][seat]}→{final[seat]}，净变化{final[seat] - first['scores'][seat]:+d}。"),
             f"按终局点数为第{rank}位（同分顺序以平台为准）；四家终局点数：{final}。"]
    for raw in replay.rounds:
        result = raw["result"]
        label = f"{'东南西北'[raw['round'] // 4]}{raw['round'] % 4 + 1}局{raw['honba']}本场"
        kind = {"ron": "荣和", "tsumo": "自摸", "exhaustive_draw": "荒牌流局",
                "abortive_draw": "途中流局", "nagashi_mangan": "流局满贯"}[result["type"]]
        if result.get("winners"):
            kind += "（开局座位" + "/".join("东南西北"[i] for i in result["winners"]) + "）"
        if result.get("from") == seat:
            kind += "，本人放铳"
        lines.append(f"{label}：{kind}；本人{raw['scores'][seat]}→{result['scores'][seat]}"
                     f"（{result['scores'][seat] - raw['scores'][seat]:+d}）。")
    return tuple(lines)
