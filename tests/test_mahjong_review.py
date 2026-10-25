"""Synthetic/offline tests; no real QQ delivery or Mahjong Soul native end-to-end claim."""

import copy
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from app.plugins.mahjong import ReportStore, handle_mahjong
from app.plugins.registry import registry
from app.services.mahjong.analysis import (
    DecisionView,
    EngineEstimate,
    EngineMetadata,
    Meld,
    RiverTile,
    analyze_visible,
    forbidden_after_call,
    own_candidates,
    response_candidates,
    safety,
    visible_counts,
)
from app.services.mahjong.records import (
    FORMAT,
    MAX_BYTES,
    RULES,
    LinkNeedsExport,
    RecordError,
    decision_input,
    import_link,
    inbox_file,
    parse_record,
    seat_number,
    share_link,
)
from app.services.mahjong.review import markdown, noten_payments, review, summary
from app.services.mahjong.tiles import (
    counts,
    dora_count,
    improvements,
    index,
    riichi_ankan_allowed,
    shanten,
    waits,
)

HAND = ["1m", "2m", "3m", "4m", "5m", "6m", "7p", "8p", "9p", "2s", "3s", "4s", "1z"]


def record(hand=None, events=None, *, seat=0, dealer=0):
    hands = [None] * 4
    hands[seat] = hand or HAND.copy()
    return {
        "format": FORMAT,
        "players": 4,
        "match": "hanchan",
        "rules": RULES,
        "rounds": [
            {
                "round": 0,
                "dealer": dealer,
                "scores": [25000] * 4,
                "hands": hands,
                "dora_indicators": ["5z"],
                "events": events
                if events is not None
                else [
                    {"type": "draw", "actor": 0, "tile": "7s"},
                    {"type": "discard", "actor": 0, "tile": "7s"},
                ],
            }
        ],
    }


def load(data, **kwargs):
    return parse_record(json.dumps(data).encode(), **kwargs)


def view(hand=None, **overrides):
    value = DecisionView(
        0,
        1,
        0,
        0,
        "hanchan",
        tuple(hand or HAND + ["7s"]),
        ((), (), (), ()),
        ((), (), (), ()),
        (None,) * 4,
        (25000,) * 4,
        ("5z",),
        69,
        0,
        0,
        "7s",
        None,
    )
    return replace(value, **overrides)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (HAND, 0),
        (HAND + ["1z"], -1),
        (["1m", "1m", "2m", "2m", "3p", "3p", "4p", "4p", "5s", "5s", "6s", "6s", "7z"], 0),
        (["1m", "9m", "1p", "9p", "1s", "9s", "1z", "2z", "3z", "4z", "5z", "6z", "7z"], 0),
    ],
)
def test_exact_shapes(text, expected):
    assert shanten(tuple(text)) == expected


def test_34567_three_sided_wait_requires_whole_hand():
    hand = ("3m", "4m", "5m", "6m", "7m", "1p", "2p", "3p", "7s", "8s", "9s", "1z", "1z")
    assert waits(hand) == {index("2m"), index("5m"), index("8m")}
    assert waits(("3m", "4m", "5m", "6m", "7m")) == set()


def test_two_sided_and_double_closed_waits():
    two = ("2m", "3m", "1p", "2p", "3p", "4p", "5p", "6p", "7s", "8s", "9s", "1z", "1z")
    double_closed = ("1m", "3m", "5m", "1p", "2p", "3p", "4p", "5p", "6p", "7s", "8s", "9s", "1z")
    assert waits(two) == {index("1m"), index("4m")}
    # Ryan-kan is a development shape, not automatically a two-sided tenpai wait.
    effective = {index(t) for t, _ in improvements(double_closed, 0, counts(double_closed))}
    assert {index("2m"), index("4m")} <= effective
    assert shanten(double_closed) == 1


def test_red_five_and_dora_cycles():
    assert index("0m") == index("5mr") == index("5m")
    assert dora_count(("0m", "1m", "1z", "5z"), ("4m", "9m", "4z", "7z")) == 5
    with pytest.raises(ValueError):
        counts(("0m", "5m", "5m", "5m", "5m"))


def test_ukeire_is_not_hidden_wall_probability():
    v = view(hand=HAND, drawn=None)
    physical = visible_counts(v)
    outs = improvements(v.hand, 0, physical)
    assert outs == (("1z", 3),)
    assert "概率" in markdown(review(load(record()), 0))


@pytest.mark.parametrize("seat", [True, -1, 4, "庄", "东一", "", "1.0"])
def test_reject_invalid_seats(seat):
    with pytest.raises(RecordError):
        seat_number(seat)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(players=3),
        lambda d: d.update(players=True),
        lambda d: d.update(rules="custom"),
        lambda d: d.update(match="sanma"),
        lambda d: d["rounds"][0].pop("scores"),
        lambda d: d["rounds"][0].update(events=[{"type": "invented"}]),
        lambda d: d["rounds"][0].update(result={"type": "chombo"}),
    ],
)
def test_bad_rules_and_missing_data(mutation):
    data = record()
    mutation(data)
    with pytest.raises(RecordError):
        load(data)


def test_match_disagreement_and_missing_own_hand():
    with pytest.raises(RecordError, match="不符"):
        load(record(), match="东风")
    with pytest.raises(RecordError, match="本人"):
        review(load(record()), 1)


@pytest.mark.parametrize(
    "payload",
    [b"", b"x" * (MAX_BYTES + 1), b"<html>page</html>", b"\xff\xfe"],
    ids=["empty", "oversize", "html", "binary"],
)
def test_bounded_non_json(payload):
    with pytest.raises(RecordError):
        parse_record(payload)


@pytest.mark.parametrize(
    "url",
    [
        "http://game.maj-soul.com/?paipu=abcdef",
        "https://127.0.0.1/?paipu=abcdef",
        "https://[::1]/?paipu=abcdef",
        "https://game.maj-soul.com.evil.test/?paipu=abcdef",
        "https://game.maj-soul.com@127.0.0.1/?paipu=abcdef",
        "https://user:pass@game.maj-soul.com/?paipu=abcdef",
        "https://game.maj-soul.com:8443/?paipu=abcdef",
        "file:///etc/passwd",
        "https://game.maj-soul.com/%2e%2e/admin?paipu=abcdef",
        "https://game.maj-soul.com/?paipu=abcdef&paipu=ghijkl",
        "https://game.maj-soul.com\\@127.0.0.1/?paipu=abcdef",
        "https://game.maj-soul.com/?paipu=" + "a" * 3000,
    ],
)
def test_ssrf_links_rejected_without_network(url, monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: pytest.fail("No DNS allowed"))
    with pytest.raises(RecordError):
        share_link(url)


def test_valid_share_link_is_honestly_unavailable(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: pytest.fail("No DNS allowed"))
    url = "https://game.maj-soul.com/1/?paipu=abcdef123_0"
    assert share_link(url) == "abcdef123_0"
    with pytest.raises(LinkNeedsExport, match="未联网"):
        import_link(url)


def test_file_boundary(tmp_path):
    inbox = tmp_path / "data/mahjong/inbox"
    inbox.mkdir(parents=True)
    (inbox / "a.json").write_text("{}")
    assert inbox_file(tmp_path, "a.json") == (inbox / "a.json").resolve()
    for value in (
        "../.env",
        "C:\\secret.json",
        "\\\\host\\share.json",
        "a.json:secret",
        "missing.json",
    ):
        with pytest.raises(RecordError):
            inbox_file(tmp_path, value)


def test_private_data_never_enters_decision_module_or_engine():
    data = record()
    data["rounds"][0]["hands"][1] = ["9m"] * 4 + ["8m"] * 4 + ["7m"] * 4 + ["6m"]
    data["rounds"][0]["wall"] = ["1z"] * 4
    data["rounds"][0]["result"] = {"type": "ron", "revealed_hands": [None, ["9m"], None, None]}
    first = review(load(data), 0)
    other = copy.deepcopy(data)
    other["rounds"][0]["hands"][1] = ["1s"] * 4 + ["2s"] * 4 + ["3s"] * 4 + ["4s"]
    other["rounds"][0]["wall"] = ["7z"] * 4
    other["rounds"][0]["result"] = {"type": "tsumo", "revealed_hands": [None, ["1s"], None, None]}
    second = review(load(other), 0)
    assert first.analysis == second.analysis
    assert first.postgame != second.postgame
    visible = decision_input(load(data), 0)
    serialized = json.dumps(asdict(visible))
    assert all(k not in serialized for k in ("wall", "result", "revealed_hands"))
    assert "hands" not in visible.rounds[0]
    with pytest.raises(TypeError):
        analyze_visible(load(data))


def test_future_draw_and_indicator_cannot_change_earlier_recommendation():
    data = record(
        hand=["1m", "1m", "1m", "2m", "3m", "4m", "2p", "3p", "4p", "5s", "6s", "7z", "7z"],
        events=[
            {"type": "draw", "actor": 0, "tile": "1m"},
            {"type": "ankan", "actor": 0, "tile": "1m"},
            {"type": "dora", "tile": "6z"},
            {"type": "draw", "actor": 0, "tile": "2z"},
            {"type": "discard", "actor": 0, "tile": "2z"},
        ],
    )
    first = review(load(data), 0)
    data["rounds"][0]["events"][2]["tile"] = "4z"
    data["rounds"][0]["events"][3]["tile"] = "3z"
    data["rounds"][0]["events"][4]["tile"] = "3z"
    second = review(load(data), 0)
    assert first.analysis.decisions[0] == second.analysis.decisions[0]
    assert first.analysis.decisions[1] != second.analysis.decisions[1]
    assert any(c.id.startswith("ankan") for c in first.analysis.decisions[0].candidates)
    assert any(c.id.startswith("discard") for c in first.analysis.decisions[0].candidates)


def test_mjai_unknown_opponent_draw_is_masked():
    events = [
        {"type": "start_game", "names": ["A", "B", "C", "D"]},
        {
            "type": "start_kyoku",
            "bakaze": "E",
            "kyoku": 1,
            "oya": 0,
            "scores": [25000] * 4,
            "tehais": [HAND, ["?"] * 13, ["?"] * 13, ["?"] * 13],
            "dora_marker": "P",
        },
        {"type": "tsumo", "actor": 0, "pai": "7s"},
        {"type": "dahai", "actor": 0, "pai": "7s", "tsumogiri": True},
        {"type": "tsumo", "actor": 1, "pai": "9m"},
        {"type": "dahai", "actor": 1, "pai": "8m"},
        {"type": "end_kyoku"},
    ]
    payload = "\n".join(json.dumps(e) for e in events).encode()
    with pytest.raises(RecordError, match="赛制"):
        parse_record(payload)
    parsed = parse_record(payload, match="半庄")
    assert decision_input(parsed, 0).rounds[0]["events"][2]["tile"] == "?"
    assert len(review(parsed, 0).analysis.decisions) == 1


def test_sequence_and_visible_conservation_rejected():
    with pytest.raises(RecordError, match="顺序"):
        review(load(record(events=[{"type": "discard", "actor": 0, "tile": "7s"}])), 0)
    data = record(hand=["1m"] * 4 + HAND[4:])
    data["rounds"][0]["events"][0]["tile"] = "1m"
    with pytest.raises(RecordError):
        review(load(data), 0)


def test_pon_includes_discard_and_counts_called_tile_once():
    hand = ["5m", "5m", "1m", "2m", "3m", "1p", "2p", "3p", "7s", "8s", "9s", "2z", "2z"]
    events = [
        {"type": "draw", "actor": 0, "tile": "?"},
        {"type": "discard", "actor": 0, "tile": "5m"},
        {"type": "pon", "actor": 1, "from": 0, "tile": "5m", "consumed": ["5m", "5m"]},
        {"type": "discard", "actor": 1, "tile": "2z"},
    ]
    result = review(load(record(hand, events, seat=1)), 1)
    decision = result.analysis.decisions[0]
    assert decision.actual == "pon:5m,5m/discard:2z"
    assert all(not c.id.endswith("discard:5m") for c in decision.candidates)
    assert any(c.id == "pass" for c in decision.candidates)
    # A later turn's visible count must not count the called river tile twice.
    v = replace(
        decision.view,
        hand=tuple(t for t in hand if t != "5m"),
        rivers=((RiverTile("5m", 2, True),), (), (), ()),
        melds=((), (Meld("pon", ("5m",) * 3),), (), ()),
    )
    assert visible_counts(v)[index("5m")] == 3


def test_chi_only_from_left_and_kuikae():
    h = ("2m", "3m", "4m", "5m", "6m", "1p", "2p", "3p", "7s", "8s", "9s", "2z", "2z")
    v = view(hand=h, drawn=None, last_discard=(3, "1m"), rivers=((), (), (), (RiverTile("1m", 2),)))
    candidates = response_candidates(v)
    assert any(c.id.startswith("chi") for c in candidates)
    assert all(not c.id.endswith("discard:4m") for c in candidates if c.id.startswith("chi:2m,3m"))
    assert not any(
        c.id.startswith("chi") for c in response_candidates(replace(v, last_discard=(1, "1m")))
    )
    assert forbidden_after_call("chi", "4m", ("2m", "3m")) == (index("1m"), index("4m"))


def test_all_three_kan_types_and_riichi_restrictions():
    h = ("1m", "1m", "1m", "2m", "3m", "4m", "2p", "3p", "4p", "5s", "6s", "7z", "7z", "1m")
    assert riichi_ankan_allowed(h, "1m", "1m", 0)
    assert not riichi_ankan_allowed(h, "7z", "1m", 0)
    ambiguous = ("1m", "1m", "1m", "2m", "2m", "2m", "3m", "3m", "3m", "4p", "5p", "6p", "7z", "1m")
    assert not riichi_ankan_allowed(ambiguous, "1m", "1m", 0)
    reached = view(hand=h, drawn="1m", riichi_at=(1, None, None, None))
    assert any(c.id == "ankan:1m" for c in own_candidates(reached))
    assert {c.id for c in own_candidates(reached) if c.id.startswith("discard")} == {"discard:1m"}
    opened = view(
        hand=("5m", "1p", "2p", "3p", "7s", "8s", "9s", "2z", "2z", "3z", "4z"),
        melds=((Meld("pon", ("5m",) * 3),), (), (), ()),
    )
    assert any(c.id == "kakan:5m" for c in own_candidates(opened))
    assert not any(
        c.id.startswith("kakan")
        for c in own_candidates(replace(opened, riichi_at=(1, None, None, None)))
    )
    tenpai = view(
        hand=h[:-1], drawn=None, last_discard=(3, "1m"), rivers=((), (), (), (RiverTile("1m", 1),))
    )
    assert any(c.id.startswith("daiminkan") for c in response_candidates(tenpai))
    assert not any(
        c.id.startswith("daiminkan") for c in response_candidates(replace(tenpai, live_tiles=0))
    )


def test_riichi_tenpai_closed_score_and_live_requirements():
    assert any(c.id == "riichi:7s" for c in own_candidates(view()))
    assert not any(c.id.startswith("riichi") for c in own_candidates(view(live_tiles=3)))
    assert not any(
        c.id.startswith("riichi") for c in own_candidates(view(scores=(900, 25000, 25000, 25000)))
    )


def test_genbutsu_suji_wall_are_targeted_and_phase_is_not_safety():
    v = view(
        riichi_at=(None, 1, None, None),
        rivers=((), (RiverTile("4m", 1),), (), (RiverTile("2z", 2),)),
    )
    assert "现物" in safety(v, "4m")
    assert "筋线索" in safety(v, "1m")
    assert "非现物" in safety(v, "1m")
    assert "单骑" in safety(v, "1m")
    assert safety(v, "1m") == safety(replace(v, live_tiles=8), "1m")
    assert v.phase == "早巡" and replace(v, live_tiles=8).phase == "晚巡"


@pytest.mark.parametrize(
    ("ready", "expected"),
    [
        ((False, False, False, False), (0, 0, 0, 0)),
        ((True, False, False, False), (3000, -1000, -1000, -1000)),
        ((True, True, False, False), (1500, 1500, -1500, -1500)),
        ((True, True, True, False), (1000, 1000, 1000, -3000)),
        ((True, True, True, True), (0, 0, 0, 0)),
    ],
)
def test_noten_all_five_counts_and_conservation(ready, expected):
    assert noten_payments(ready) == expected
    assert sum(expected) == 0


def test_endgame_does_not_mix_penalties():
    data = record()
    data["rounds"][0]["result"] = {
        "type": "exhaustive_draw",
        "tenpai": [True, False, False, False],
        "deltas": [3000, -1000, -1000, -1000],
    }
    result = review(load(data), 0)
    assert any("一致" in s for s in result.postgame)
    for kind in ("abortive_draw", "nagashi_mangan"):
        data["rounds"][0]["result"] = {"type": kind}
        other = review(load(data), 0)
        assert other.analysis == result.analysis
        assert "不能按未听罚符" in "".join(other.postgame) or "不应用" in "".join(other.postgame)


def test_optional_engine_receives_only_frozen_view():
    class Engine:
        metadata = EngineMetadata("test-local", "1", "0" * 64, "test-only", "qualitative")

        def estimate(self, v, candidates):
            assert isinstance(v, DecisionView)
            assert set(asdict(v)) == set(DecisionView.__dataclass_fields__)
            assert not hasattr(v, "result") and not hasattr(v, "opponent_hands")
            return (EngineEstimate(candidates[0].id, "测试估计，不代表胜率"),)

    result = review(load(record()), 0, engine=Engine())
    assert result.analysis.engine_metadata.name == "test-local"
    assert "引擎估计，非概率" in markdown(result)


def test_report_scope_and_summary():
    result = review(load(record()), 0)
    store = ReportStore()
    owner = ("bot", "group", "user")
    job = store.put(owner, result)
    assert store.get(owner, job) is result
    for other in (("bot2", "group", "user"), ("bot", "group2", "user"), ("bot", "group", "user2")):
        with pytest.raises(RecordError):
            store.get(other, job)
    assert len(summary(result, job)) < 600
    assert "未声称训练完成" in markdown(result)
    assert result.coach_prompt_sha256 and result.coach_context


async def test_plugin_dispatch_preserves_hello_and_no_external_llm(tmp_path, monkeypatch):
    import app.plugins.mahjong as plugin

    state = SimpleNamespace()
    ctx = SimpleNamespace(
        request=SimpleNamespace(app=SimpleNamespace(state=state)),
        event={"self_id": "test-bot"},
        group_id="g",
        user_id="u",
        is_admin=False,
    )
    monkeypatch.setattr(plugin, "_root", lambda _: tmp_path)
    inbox = tmp_path / "data/mahjong/inbox"
    inbox.mkdir(parents=True)
    (inbox / "sample.json").write_text(json.dumps(record()))
    outcome = await registry.dispatch("/牌谱分析 座位=东 赛制=半庄 文件=sample.json", ctx)
    assert outcome[0] == "mahjong_review" and "1决策" in outcome[1]
    job = next(iter(state.mahjong_reports.jobs))
    ctx.args = f"详情 {job} 1"
    detail = await handle_mahjong(ctx)
    assert "第1/" in detail and len(detail) < 3500
    ctx.args = f"导出 {job}"
    assert "已保存" in await handle_mahjong(ctx)
    assert (tmp_path / f"data/mahjong/reports/{job}.md").exists()
    registry.load_modules("app.plugins.hello")
    assert await registry.dispatch("/hello", ctx) == ("hello", "你好，我是 qqchat robot。")
