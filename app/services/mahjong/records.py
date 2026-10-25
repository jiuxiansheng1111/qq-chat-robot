"""Bounded import into an explicit event schema; native Mahjong Soul RPC is not guessed."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.services.mahjong.tiles import tile

MAX_BYTES = 2 * 1024 * 1024
MAX_EVENTS = 1600
FORMAT = "qq-mahjong-review/v1"
RULES = "majsoul-standard-4p"
OFFICIAL_HOSTS = frozenset(
    {
        "game.maj-soul.com",
        "game.mahjongsoul.com",
        "mahjongsoul.game.yo-star.com",
    }
)
MATCHES = {"东风": "tonpuu", "半庄": "hanchan", "tonpuu": "tonpuu", "hanchan": "hanchan"}
SEATS = {"东": 0, "南": 1, "西": 2, "北": 3, "0": 0, "1": 1, "2": 2, "3": 3}


class RecordError(ValueError):
    """Safe user-facing validation failure, never includes the submitted record."""


class LinkNeedsExport(RecordError):
    pass


@dataclass(frozen=True)
class Replay:
    match: str
    rounds: tuple[dict, ...]
    source: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class VisibleReplay:
    match: str
    seat: int
    rounds: tuple[dict, ...]


def decision_input(replay: Replay, seat: int) -> VisibleReplay:
    """One-way boundary: no result, other concealed hand, wall, names, or arbitrary keys."""
    rounds = []
    for raw in replay.rounds:
        own = raw["hands"][seat]
        if own is None or "?" in own:
            raise RecordError("缺少本人完整起手牌，不能计算向听和逐巡决策")
        events = []
        for original in raw["events"]:
            event = dict(original)
            if event["type"] == "draw" and event["actor"] != seat:
                event["tile"] = "?"
            events.append(event)
        rounds.append(
            {
                "round": raw["round"],
                "dealer": raw["dealer"],
                "scores": tuple(raw["scores"]),
                "own_hand": tuple(own),
                "dora_indicators": tuple(raw["dora_indicators"]),
                "honba": raw["honba"],
                "kyotaku": raw["kyotaku"],
                "events": tuple(events),
            }
        )
    return VisibleReplay(replay.match, seat, tuple(rounds))


def seat_number(value: str | int) -> int:
    if isinstance(value, bool) or str(value) not in SEATS:
        raise RecordError("本人座位必须是开局固定座位 东/南/西/北 或 0..3，不是当前局自风")
    return SEATS[str(value)]


def match_name(value: object) -> str:
    if not isinstance(value, str) or value not in MATCHES:
        raise RecordError("赛制必须明确选择 东风 或 半庄")
    return MATCHES[value]


def share_link(value: str) -> str:
    """Validate but NEVER fetch a share page, resolve DNS, authenticate, or upload anything.

    No untrusted URL reaches HTTP at all, hence no redirect / DNS-rebinding SSRF surface.
    A future native adapter must independently pin public IPs and bound compressed bytes/time.
    """
    if len(value) > 2048 or any(ord(c) < 33 for c in value) or "\\" in value:
        raise RecordError("牌谱链接过长或包含非法字符")
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.hostname not in OFFICIAL_HOSTS
            or url.username is not None
            or url.password is not None
            or url.port not in (None, 443)
            or "%" in url.netloc
            or url.path not in ("/", "/1/", "/2/")
        ):
            raise RecordError("仅接受官方雀魂 HTTPS 牌谱分享链接；不接受账号凭据、IP 或其他域名")
        query = parse_qs(url.query, max_num_fields=12, strict_parsing=False)
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, RecordError):
            raise
        raise RecordError("牌谱链接格式不正确") from exc
    values = query.get("paipu", [])
    if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{6,256}", values[0]):
        raise RecordError("官方链接中缺少合法的 paipu 标识")
    return values[0]


def import_link(value: str) -> Replay:
    share_link(value)
    raise LinkNeedsExport(
        "已识别官方雀魂牌谱链接。本地尚无已验证的原生牌谱解码器，未联网抓取；"
        "不能仅凭分享网页复盘。请使用本地导出的 qq-mahjong-review/v1 JSON 或 mjai JSON/JSONL。"
        "不需要账号密码，也不会将牌谱交给第三方。"
    )


def _tiles(values: object, *, allow_hidden: bool = False) -> list[str]:
    if not isinstance(values, list) or len(values) > 18:
        raise RecordError("手牌/副露字段必须是有限牌数组")
    try:
        return ["?" if allow_hidden and t == "?" else tile(t) for t in values]
    except ValueError as exc:
        raise RecordError("导出文件含非法牌编码") from exc


def _int(value: object, lower: int, upper: int, label: str) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise RecordError(f"{label}格式或范围错误")
    return value


def _event(event: object) -> dict:
    if not isinstance(event, dict):
        raise RecordError("事件必须是对象")
    kind = event.get("type")
    if kind not in {
        "draw",
        "discard",
        "riichi",
        "chi",
        "pon",
        "daiminkan",
        "ankan",
        "kakan",
        "dora",
        "ron",
        "tsumo",
        "riichi_accept",
    }:
        raise RecordError("存在未支持的事件；请勿忽略事件后继续假装完整复盘")
    if kind == "dora":
        try:
            return {"type": kind, "tile": tile(event.get("tile"))}
        except ValueError as exc:
            raise RecordError("宝牌指示牌缺失或错误") from exc
    actor = _int(event.get("actor"), 0, 3, "事件座位")
    result = {"type": kind, "actor": actor}
    if kind not in {"ron", "tsumo", "riichi_accept"}:
        try:
            # Opponents' draw identities are removed later BEFORE creating decision states.
            result["tile"] = (
                "?" if event.get("tile") == "?" and kind == "draw" else tile(event.get("tile"))
            )
        except ValueError as exc:
            raise RecordError("事件牌缺失或错误") from exc
    if kind in {"chi", "pon", "daiminkan"}:
        result["from"] = _int(event.get("from"), 0, 3, "鸣牌来源座位")
        result["consumed"] = _tiles(event.get("consumed"))
    if kind == "riichi":
        accepted = event.get("accepted", True)
        if type(accepted) is not bool:
            raise RecordError("立直成立标记必须为布尔值")
        result["accepted"] = accepted
    if kind in {"discard", "riichi"} and "tsumogiri" in event:
        if type(event["tsumogiri"]) is not bool:
            raise RecordError("摸切标记必须是布尔值或省略表示未知")
        result["tsumogiri"] = event["tsumogiri"]
    if kind == "ankan" and "consumed" in event:
        result["consumed"] = _tiles(event["consumed"])
        if (
            len(result["consumed"]) != 4
            or len({tile(t).replace("0", "5") for t in result["consumed"]}) != 1
        ):
            raise RecordError("暗杠须消耗四枚相同牌（赤五与普通五合并）")
    return result


def _normalized(data: dict, requested_match: str | None) -> Replay:
    if data.get("format") != FORMAT:
        raise RecordError("不支持此导出格式；原生 protobuf、天凤原始 log 和网页 HTML 尚未实现")
    if type(data.get("players")) is not int or data["players"] != 4:
        raise RecordError("仅支持四人麻将；三麻、缺人或人数不明不能按四麻分析")
    if data.get("rules") != RULES:
        raise RecordError("规则必须明确是 majsoul-standard-4p；友人房自定义规则暂不支持")
    match = match_name(data.get("match"))
    if requested_match and match != requested_match:
        raise RecordError("所选东风/半庄与导出文件不符")
    raw_rounds = data.get("rounds")
    if not isinstance(raw_rounds, list) or not 1 <= len(raw_rounds) <= 32:
        raise RecordError("需要 1..32 局牌谱")
    total = 0
    rounds = []
    warnings = []
    for raw in raw_rounds:
        if not isinstance(raw, dict):
            raise RecordError("局数据必须是对象")
        round_index = _int(raw.get("round"), 0, 15, "场风局号")
        dealer = _int(raw.get("dealer"), 0, 3, "庄家座位")
        if dealer != round_index % 4:
            raise RecordError("庄家与局号不符；内部格式座位必须先统一为开局东家=0的固定顺序")
        scores = raw.get("scores")
        if not isinstance(scores, list) or len(scores) != 4:
            raise RecordError("缺少四家当局开始点数，不能编造排名条件")
        scores = [_int(v, -100_000, 1_000_000, "点数") for v in scores]
        initial = raw.get("hands")
        if not isinstance(initial, list) or len(initial) != 4:
            raise RecordError("需要四座位起手字段；未知手牌可填 null，不能缺本人手牌")
        hands = [None if h is None else _tiles(h, allow_hidden=True) for h in initial]
        if any(h is not None and len(h) != 13 for h in hands):
            raise RecordError("此格式每家起手须为13枚，庄家首摸也必须有 draw 事件")
        indicators = _tiles(raw.get("dora_indicators"))
        if len(indicators) != 1:
            raise RecordError("开局必须提供一枚当时已公开的宝牌指示牌")
        events = raw.get("events")
        if not isinstance(events, list):
            raise RecordError("缺少有序摸打事件，不能对截图式手牌声称逐巡复盘")
        total += len(events)
        if total > MAX_EVENTS:
            raise RecordError("事件超过1600条；请按局拆分本地导出文件")
        result = raw.get("result")
        if result is not None:
            if not isinstance(result, dict) or result.get("type") not in {
                "exhaustive_draw",
                "abortive_draw",
                "draw_unknown",
                "ron",
                "tsumo",
                "nagashi_mangan",
            }:
                raise RecordError("终局类型未知；不将犯规罚点和荒牌流局罚符混用")
        else:
            warnings.append(f"第{len(rounds) + 1}局没有终局数据，只能分析已提供片段")
        rounds.append(
            {
                "round": round_index,
                "dealer": dealer,
                "scores": scores,
                "hands": hands,
                "dora_indicators": indicators,
                "events": [_event(e) for e in events],
                "honba": _int(raw.get("honba", 0), 0, 100, "本场"),
                "kyotaku": _int(raw.get("kyotaku", 0), 0, 100, "供托棒"),
                "result": result,
            }
        )
    return Replay(match, tuple(rounds), "normalized-json", tuple(warnings))


def _mjai(events: list, requested_match: str | None) -> Replay:
    if requested_match is None:
        raise RecordError("mjai通常不包含东风/半庄元数据，必须由本人明确选择赛制")
    if len(events) > MAX_EVENTS + 100:
        raise RecordError("mjai事件数量过多")
    rounds = []
    current = None
    started = False
    finished = False
    seat_origin = None
    pending_reach: set[int] = set()
    aliases = {
        "tsumo": "draw",
        "dahai": "discard",
        "chi": "chi",
        "pon": "pon",
        "daiminkan": "daiminkan",
        "ankan": "ankan",
        "kakan": "kakan",
    }
    for event in events:
        if not isinstance(event, dict):
            raise RecordError("mjai事件必须是对象")
        kind = event.get("type")
        if finished:
            raise RecordError("mjai end_game 后仍有事件")
        if kind == "start_game":
            if started or current is not None:
                raise RecordError("mjai重复start_game")
            names = event.get("names")
            if not isinstance(names, list) or len(names) != 4:
                raise RecordError("mjai start_game须明确四家，不支持三麻")
            started = True
        elif kind == "start_kyoku":
            if not started:
                raise RecordError("mjai缺少start_game四家声明")
            if current is not None:
                raise RecordError("上一局缺少 end_kyoku")
            bakaze = event.get("bakaze")
            if bakaze not in ("E", "S", "W", "N"):
                raise RecordError("mjai场风错误")
            kyoku = _int(event.get("kyoku"), 1, 4, "mjai局号")
            dealer = _int(event.get("oya"), 0, 3, "mjai庄家")
            origin = (dealer - kyoku + 1) % 4
            if seat_origin is None:
                seat_origin = origin
            elif seat_origin != origin:
                raise RecordError("mjai庄家与局号的固定座位映射不一致")
            current = {
                "round": ("ESWN".index(bakaze) * 4 + kyoku - 1),
                "dealer": dealer,
                "scores": event.get("scores"),
                "hands": event.get("tehais"),
                "honba": event.get("honba", 0),
                "kyotaku": event.get("kyotaku", 0),
                "dora_indicators": [event.get("dora_marker")],
                "events": [],
            }
            pending_reach.clear()
        elif kind == "end_game":
            if current is not None:
                raise RecordError("mjai end_game 前缺少end_kyoku")
            finished = True
        elif kind == "none":
            continue
        elif current is None:
            raise RecordError("mjai事件出现在 start_kyoku 之外")
        elif kind == "reach":
            who = _int(event.get("actor"), 0, 3, "立直座位")
            if who in pending_reach:
                raise RecordError("重复立直宣言")
            pending_reach.add(who)
        elif kind == "reach_accepted":
            current["events"].append({"type": "riichi_accept", "actor": event.get("actor")})
        elif kind in aliases:
            mapped = {"type": aliases[kind], "actor": event.get("actor"), "tile": event.get("pai")}
            if kind == "dahai" and "tsumogiri" in event:
                mapped["tsumogiri"] = event["tsumogiri"]
            if kind == "ankan":
                consumed = _tiles(event.get("consumed"))
                if len(consumed) != 4:
                    raise RecordError("标准mjai暗杠需要consumed[4]")
                mapped.update(tile=consumed[0], consumed=consumed)
            if kind == "dahai" and event.get("actor") in pending_reach:
                mapped["type"] = "riichi"
                mapped["accepted"] = False
                pending_reach.remove(event["actor"])
            if kind in {"chi", "pon", "daiminkan"}:
                mapped.update({"from": event.get("target"), "consumed": event.get("consumed")})
            current["events"].append(mapped)
        elif kind == "dora":
            current["events"].append({"type": "dora", "tile": event.get("dora_marker")})
        elif kind == "hora":
            win = "tsumo" if event.get("actor") == event.get("target") else "ron"
            current["events"].append({"type": win, "actor": event.get("actor")})
            current["result"] = {"type": win}
        elif kind == "ryukyoku":
            reason = event.get("reason")
            if reason not in (
                None,
                "fanpai",
                "kyushukyuhai",
                "sufonrenta",
                "suchareach",
                "sukaikan",
                "sanchaho",
            ):
                raise RecordError("mjai流局原因未明确，不能猜测罚符")
            result = {
                "type": "draw_unknown"
                if reason is None
                else "exhaustive_draw"
                if reason == "fanpai"
                else "abortive_draw"
            }
            if "tenpais" in event:
                result["tenpai"] = event["tenpais"]
            if "deltas" in event:
                result["deltas"] = event["deltas"]
            current["result"] = result
        elif kind == "end_kyoku":
            if pending_reach:
                raise RecordError("立直宣言后缺少对应舍牌")
            rounds.append(current)
            current = None
        else:
            raise RecordError("未支持的mjai事件；请勿静默丢弃操作")
    if current is not None:
        rounds.append(current)
    if seat_origin:
        for raw in rounds:
            for field in ("scores", "hands"):
                values = raw[field]
                if not isinstance(values, list) or len(values) != 4:
                    raise RecordError("mjai缺少四家点数/手牌，不能重排座位")
                raw[field] = [values[(s + seat_origin) % 4] for s in range(4)]
            raw["dealer"] = (raw["dealer"] - seat_origin) % 4
            for e in raw["events"]:
                for key in ("actor", "from"):
                    if key in e:
                        e[key] = (_int(e[key], 0, 3, "mjai事件座位") - seat_origin) % 4
            for field in ("tenpai", "deltas"):
                values = raw.get("result", {}).get(field)
                if isinstance(values, list) and len(values) == 4:
                    raw["result"][field] = [values[(s + seat_origin) % 4] for s in range(4)]
    replay = _normalized(
        {
            "format": FORMAT,
            "players": 4,
            "rules": RULES,
            "match": requested_match,
            "rounds": rounds,
        },
        requested_match,
    )
    return Replay(
        replay.match,
        replay.rounds,
        "mjai",
        replay.warnings
        + (
            "mjai按convlog/gimite基础事件方言导入；仅据格式无法证明来源是雀魂，采用用户选择的保守四麻规则子集",
            f"固定座位0东1南2西3北对应原mjai actor：{tuple((s + (seat_origin or 0)) % 4 for s in range(4))}",
            *(() if finished else ("mjai未以end_game结束，作为牌谱片段处理",)),
        ),
    )


def parse_record(payload: bytes, *, match: str | None = None) -> Replay:
    if not payload or len(payload) > MAX_BYTES:
        raise RecordError("导出文件为空或超过2MiB")
    requested = match_name(match) if match is not None else None
    try:
        text = payload.decode("utf-8-sig")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RecordError("请输入UTF-8 JSON/JSONL导出文件；压缩包、二进制和网页均不支持") from exc
    if isinstance(data, dict):
        if "log" in data and "format" not in data:
            from app.services.mahjong.tenhou import to_mjai

            converted = _mjai(to_mjai(data, requested), requested)
            return Replay(
                converted.match,
                converted.rounds,
                "tenhou.net/6-local",
                converted.warnings
                + (
                    (
                        "本地tenhou.net/6兼容导入；只能证明导出格式兼容，不证明是雀魂原生数据或当前客户端转换已实测。"
                        "杠宝牌公开时机采用本地转换约定，非所有平台细则认证"
                    ),
                ),
            )
        return _normalized(data, requested)
    if isinstance(data, list):
        return _mjai(data, requested)
    raise RecordError("牌谱顶层必须为对象或mjai事件数组")


def read_local(path: Path) -> bytes:
    if path.suffix.lower() not in {".json", ".jsonl", ".mjson"}:
        raise RecordError("仅接受 .json/.jsonl/.mjson 本地导出文件")
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise RecordError("无法读取导出文件") from exc
    if len(data) > MAX_BYTES:
        raise RecordError("导出文件超过2MiB")
    return data


def inbox_file(root: Path, basename: str) -> Path:
    """QQ callers cannot read arbitrary server paths, UNC, traversal, or reparse points."""
    if (
        not basename
        or len(basename) > 100
        or not re.fullmatch(r"[\w.-]+", basename)
        or basename in {".", ".."}
        or ":" in basename
    ):
        raise RecordError("文件名只能是导入箱中的 JSON 文件名，不能含目录或网络路径")
    inbox = root / "data" / "mahjong" / "inbox"
    path = inbox / basename
    try:
        resolved = path.resolve(strict=True)
        if resolved.parent != inbox.resolve(strict=True) or not resolved.is_file():
            raise RecordError("文件不在本地牌谱导入箱内")
        # Also reject directory junctions pointing outside the intended project.
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        if isinstance(exc, RecordError):
            raise
        raise RecordError("导入箱中未找到文件，或路径指向项目之外") from exc
    return resolved
