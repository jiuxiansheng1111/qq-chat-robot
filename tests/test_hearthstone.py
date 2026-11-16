import base64
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest

from app import astrbot_hearthstone as bridge
from app.services.hearthstone.decks import analyze, decode, replacement_candidates, rune_info
from app.services.hearthstone.service import (
    HearthstoneService,
    archetype_matches,
    rank_rows,
    source_meta,
)

REAL_CODE = "AAECAZ8FBsmgBLSBB/WYB53dB5f6B4GKCAzunwStoASd1ASF/gXP/gbLqQe+sge/sgeY3QeZ3Qfx8Aev9gcA"
SIDEBOARD_CODE = "AAECAR8ImKAEzZ4GpfwGkoMHqJcHg8AHrtgHp/cHC6mfBOD4Ba+SB8yWB96WB9eXB/2bB/qwB9faB9vaB5yCCAABA9/+Bq7YB4OEB67YB7qVB67YBwAA"


def code(cards, mode=2, sideboards=()):
    values = [0, 1, mode, 1, 7]
    for fixed in (1, 2, None):
        group = [(card_id, count) for card_id, count in cards if count == fixed or fixed is None and count not in (1, 2)]
        values.append(len(group))
        for card_id, count in group:
            values.append(card_id)
            if fixed is None:
                values.append(count)
    values.append(bool(sideboards))
    if sideboards:
        values += [len(sideboards)]
        for card_id, owner in sideboards:
            values += [card_id, owner]
        values += [0, 0]
    raw = bytearray()
    for number in values:
        while number >= 128:
            raw.append((number & 127) | 128)
            number >>= 7
        raw.append(number)
    return base64.b64encode(raw).decode()


def card(n, *, name=None, mana=2, faction="Deathknight", runes=(0, 0, 0), standard=True, rarity="普通", dust=40, effect="抽一张牌。"):
    return {"id": n, "dbf_id": n, "name": name or f"测试牌{n}", "english_name": f"Card {n}",
            "mana": mana, "class": faction, "type": "法术", "effect": effect,
            "rarity": rarity, "dust": dust, "standard": standard, "wild": True,
            "collectible": True, "runes": dict(zip(("blood", "frost", "unholy"), runes, strict=True)),
            "tribe_or_school": "冰霜", "set_code": "TEST", "set": "测试", "updated": 1}


def dk_deck():
    cards = {n: card(n) for n in range(100, 115)}
    cards[100] = card(100, runes=(2, 0, 0))
    cards[101] = card(101, runes=(0, 1, 0))
    return code([(n, 2) for n in cards]), cards


def test_real_yingdi_code_and_modern_sideboards():
    plain = decode(REAL_CODE)
    assert plain["mode"] == "standard"
    assert sum(c[1] for c in plain["cards"]) == 30
    side = decode(SIDEBOARD_CODE)
    assert len(side["sideboards"]) == 3
    assert sum(c[1] for c in side["cards"]) == 30
    assert {c[2] for c in side["sideboards"]} <= set(dict(side["cards"]))


@pytest.mark.parametrize("bad", ["hello", "A" * 2000, REAL_CODE[:-7], "AAECAQ" + "/" * 80])
def test_corrupt_and_oversized_codes_are_rejected(bad):
    with pytest.raises(ValueError):
        decode(bad)


def test_death_knight_three_runes_are_maxima_not_card_count():
    deck_code, cards = dk_deck()
    deck = analyze(deck_code, cards)
    assert deck["rune_allocation"]["summary"] == "鲜血2 / 冰霜1"
    assert deck["rune_allocation"]["used_slots"] == 3
    assert deck["validation"]["runes_valid"]
    cards[102] = card(102, runes=(0, 0, 1))
    invalid = analyze(deck_code, cards)
    assert not invalid["validation"]["runes_valid"]
    assert invalid["rune_allocation"]["used_slots"] == 4


def test_three_same_color_and_unassigned_rune_are_explicit():
    assert rune_info([card(1, runes=(3, 0, 0))])["valid"]
    info = rune_info([card(1, runes=(1, 1, 0))])
    assert info["free_slots"] == 1 and info["required"]["unholy"] == 0


def test_sideboard_runes_count_and_are_not_in_main_curve():
    _, cards = dk_deck()
    cards[200] = card(200, mana=8, runes=(0, 0, 1))
    deck = analyze(code([(n, 2) for n in range(100, 115)], sideboards=[(200, 100)]), cards)
    assert not deck["validation"]["runes_valid"]
    assert deck["total_cards"] == 30 and deck["mana_curve"]["7+"] == 0
    assert deck["dust_main"] == 1200 and deck["dust_sideboard"] == 40


def test_standard_never_accepts_wild_card_and_unknown_is_not_complete():
    deck_code, cards = dk_deck()
    cards[100]["standard"] = False
    assert analyze(deck_code, cards)["validation"]["outside_current_card_pool"] == ["测试牌100"]
    del cards[101]
    result = analyze(deck_code, cards)
    assert not result["validation"]["complete"]
    assert result["validation"]["unknown_dbf_ids"] == [101]
    with pytest.raises(ValueError, match="不一致"):
        analyze(deck_code, cards, expected_mode="wild")


def test_current_core_original_print_is_resolved():
    deck_code, cards = dk_deck()
    cards[100]["standard"] = False
    cards[200] = {**card(200, name="测试牌100"), "set_code": "CORE", "dust": 0}
    result = analyze(deck_code, cards)
    assert not result["validation"]["outside_current_card_pool"]
    assert next(c for c in result["cards"] if c["code_dbf_id"] == 100)["dbf_id"] == 200


def test_replacements_obey_class_budget_runes_and_copy_limit():
    deck_code, cards = dk_deck()
    extra = {
        201: card(201, name="合法替换", runes=(1, 1, 0)),
        202: card(202, name="第四枚符文", runes=(0, 0, 1)),
        203: card(203, name="别的职业", faction="Mage"),
        204: card(204, name="狂野卡", standard=False),
        205: card(205, name="超出预算", dust=400),
    }
    result = replacement_candidates(analyze(deck_code, cards), "测试牌110", cards | extra, budget=100)
    assert [c["name"] for c in result["candidates"]] == ["合法替换"]


def test_removing_last_rune_card_does_not_silently_change_selected_runes():
    deck_code, cards = dk_deck()
    # One copy is removed, so the other copy still requires two Blood anyway.
    extra = {201: card(201, runes=(0, 0, 3)), 202: card(202, runes=(1, 0, 0))}
    result = replacement_candidates(analyze(deck_code, cards), "测试牌100", cards | extra)
    assert [c["dbf_id"] for c in result["candidates"]] == [202]


def test_highlander_and_odd_filters():
    cards = {n: card(n, mana=3, faction="Mage") for n in range(100, 130)}
    cards[100].update(name="噬月者巴库", rarity="传说")
    cards[101].update(effect="如果你的牌库里没有相同的牌，抽一张牌。")
    deck = analyze(code([(n, 1) for n in cards], mode=1), cards)
    extra = {201: card(201, mana=4, faction="Mage"), 202: card(202, mana=3, faction="Mage")}
    result = replacement_candidates(deck, "测试牌110", cards | extra)
    assert [c["dbf_id"] for c in result["candidates"]] == [202]


def test_rankings_use_real_counts_and_ignore_tiny_or_invalid_samples():
    rows = [{"archetype": "tiny", "winrate": 99, "popularity": 20, "games": 5},
            {"archetype": "popular", "winrate": 53, "popularity": 20, "games": 10000},
            {"archetype": "winning", "winrate": 60, "popularity": 2, "games": 2000},
            {"archetype": "bad", "winrate": float("nan"), "popularity": 2, "games": 2000}]
    assert [r["archetype"] for r in rank_rows(rows, "winrate", 500)] == ["winning", "popular"]
    assert rank_rows(rows, "popularity", 500)[0]["archetype"] == "popular"


@pytest.mark.parametrize("stamp,stale", [((datetime.now(UTC)-timedelta(days=3)).isoformat(), False),
                                         (datetime.now(UTC).isoformat(), True), ("", False)])
def test_expired_or_missing_snapshot_is_not_current(stamp, stale):
    with pytest.raises(ValueError):
        source_meta({"meta": {"fetched_at": stamp, "stale": stale}})


@pytest.mark.parametrize("name,query,expected", [("Pirate Warrior", "海盗战", True),
                                                 ("Pirate Rogue", "海盗战", False),
                                                 ("Even DK", "偶数DK", True),
                                                 ("XL Highlander Warrior", "宇宙战", True),
                                                 ("Quest Mage", "宇宙法", False)])
def test_strength_names_resolve_candidates_without_mixing_classes(name, query, expected):
    assert archetype_matches(name, query) is expected


async def test_stat_failure_returns_community_without_inventing_winrate(tmp_path):
    s = HearthstoneService(tmp_path)
    s.api = AsyncMock(side_effect=httpx.ConnectError("offline"))
    s.yingdi_decks = AsyncMock(return_value={"status": "ok", "decks": [{"winrate": None}], "sort": "community"})
    result = await s.recommend("wild", sort="winrate")
    assert result["status"] == "partial" and result["sort"] == "community"
    assert result["decks"][0]["winrate"] is None


def event(text, *, at="bot", group="group", user="user", platform="aiocqhttp"):
    extras = {}
    return NS(get_message_str=lambda: text, get_group_id=lambda: group,
              get_platform_name=lambda: platform, unified_msg_origin=f"{platform}:{group or user}",
              is_at_or_wake_command=False, get_extra=lambda k, d=None: extras.get(k, d),
              set_extra=lambda k, v: extras.__setitem__(k, v),
              message_obj=NS(message=[], raw_message={"self_id": "bot", "user_id": user,
                  "group_id": group, "message_type": "group" if group else "private",
                  "message": [{"type": "at", "data": {"qq": at}}] if at else []}))


def plugin():
    return NS(runtime=NS(), _legacy_event_allowed=AsyncMock(return_value=True))


@pytest.mark.parametrize("text,action,mode", [
    ("炉石卡组 标准 胜率", "recommend", "standard"), ("炉石卡组 狂野 热度", "recommend", "wild"),
    ("查卡 标准 火球术", "card", "standard"), ("海盗战强度怎么样，推不推荐", "evaluate", ""),
    ("狂野海盗战现在强不强", "evaluate", "wild"), ("炉石偶数DK值得合吗", "evaluate", ""),
    ("卡组分析 " + REAL_CODE, "analyze", ""),
])
def test_natural_requests(text, action, mode):
    result = bridge.intent(text)
    assert result["action"] == action and result["mode"] == mode


@pytest.mark.parametrize("text,mode,name", [
    ("查询卡牌斯卡布斯刀油", "", "斯卡布斯刀油"), ("查卡斯卡布斯刀油", "", "斯卡布斯刀油"),
    ("帮我查一下炉石卡片《斯卡布斯·刀油》", "", "斯卡布斯·刀油"), ("/卡片查询：刀油", "", "刀油"),
    ("炉石卡牌查询 狂野 火球术", "wild", "火球术"), ("查卡 狂野炎术师", "", "狂野炎术师"),
])
def test_card_shortcuts_preserve_the_actual_name(text, mode, name):
    assert bridge.intent(text) == {"action": "card", "query": name, "mode": mode}


async def test_names_aliases_typos_and_separate_printings(tmp_path):
    from app.services.hearthstone.service import utc_now

    data = HearthstoneService(tmp_path)
    data.catalog_at = utc_now()
    data.catalog = {
        1: card(1, name="斯卡布斯·刀油"), 2: card(2, name="修补匠的磨刀油"),
        3: card(3, name="了不起的杰弗里斯"),
        4: card(4, name="火球术", standard=False), 5: card(5, name="火球术"),
        6: card(6, name="大法师", effect="将一张火球术置入你的手牌。"),
    }
    async def ids(name, mode=""):
        return [c["dbf_id"] for c in (await data.lookup(name, mode))["cards"]]

    assert await ids("斯卡布斯刀油") == [1] and await ids("斯刀") == [1] and await ids("姐夫") == [3]
    assert set(await ids("刀油")) == {1, 2}
    fuzzy = await data.lookup("斯卡布斯刀由")
    assert fuzzy["similar"] and fuzzy["cards"][0]["dbf_id"] == 1
    assert await ids("火球术") == [5, 4] and await ids("火球术", "standard") == [5]
    assert await ids("置入你的手牌") == [6] and await ids("无关的完整名字") == []


async def test_legacy_catalog_fetches_only_the_confirmed_printings_face(monkeypatch, tmp_path):
    import app.services.hearthstone.service as storage

    data = HearthstoneService(tmp_path)
    chosen = card(5, name="火球术")
    data.catalog = {5: chosen}
    data.api = AsyncMock(return_value={"data": {"cards": [
        {"gameid": "4", "img": "https://pic.iyingdi.com/other.png"},
        {"gameid": "5", "img": "http://static.iyingdi.cn/selected.png"},
    ]}})
    download = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfixture")
    monkeypatch.setattr(storage, "request_bytes", download)
    assert await data.card_image(chosen) == download.return_value
    assert download.await_args.args[2] == "https://static.iyingdi.cn/selected.png"
    chosen["image_url"] = "https://elsewhere.invalid/image.png"
    with pytest.raises(ValueError):
        await data.card_image(chosen)
    assert download.await_count == 1


@pytest.mark.parametrize("text", ["炉石帮助", "不要推荐炉石卡组", "他说炉石卡组很强", "今天天气怎么样", "视频怎么用"])
def test_help_and_unrelated_chat_are_not_executed(text):
    assert bridge.intent(text) is None


async def test_acl_and_platform_precede_data_calls(monkeypatch):
    s = NS(lookup=AsyncMock())
    monkeypatch.setattr(bridge, "service", lambda _: s)
    p = plugin()
    p._legacy_event_allowed.return_value = False
    assert (await bridge.query(p, event("查卡 火球术"), action="card", query="火球术"))["status"] == "denied"
    assert (await bridge.query(p, event("查卡 火球术", platform="bilibili"), action="card", query="火球术"))["status"] == "unavailable"
    s.lookup.assert_not_awaited()


async def test_prepare_once_and_preserves_recipient(monkeypatch):
    query_mock = AsyncMock(return_value={"status": "ok", "cards": []})
    monkeypatch.setattr(bridge, "query", query_mock)
    p = plugin()
    for at in (None, "other"):
        request = NS(system_prompt="original", func_tool=None)
        await bridge.prepare(p, event("查卡 火球术", at=at), request)
        assert request.system_prompt == "original"
    query_mock.assert_not_awaited()
    request = NS(system_prompt="original", func_tool=None)
    e = event("查卡 火球术")
    await bridge.prepare(p, e, request)
    await bridge.prepare(p, e, request)
    assert query_mock.await_count == 1 and e.get_extra("qqchat_hearthstone_prepared")


def test_recent_deck_never_crosses_user_group_or_platform():
    p = plugin()
    e = event("x")
    bridge._remember(p, e, {"deck": {"code": REAL_CODE}})
    assert bridge._recent(p, e) == [REAL_CODE]
    assert not bridge._recent(p, event("x", user="another"))
    assert not bridge._recent(p, event("x", group="another"))
    assert not bridge._recent(p, event("x", platform="bilibili"))


@pytest.mark.parametrize("name", ["DeathKnight", "death knight", "death_knight", "DK", "死亡骑士"])
def test_death_knight_class_spelling(name):
    assert bridge.canonical_class(name) == "Deathknight"


def test_dk_natural_request_selects_the_actual_class():
    assert bridge.intent("炉石卡组 标准 death knight")["player_class"] == "Deathknight"
    assert bridge.intent("炉石卡组 狂野 DK")["player_class"] == "Deathknight"


async def test_mode_specific_followup_uses_that_modes_recommendation(monkeypatch):
    standard, cards = dk_deck()
    wild = code([(n, 2) for n in cards], mode=1)
    p, e = plugin(), event("分析狂野第一套")
    bridge._remember(p, e, {"results": [{"decks": [{"deck": {"code": standard}}, {"deck": {"code": wild}}]}]})
    evaluate = AsyncMock(return_value={"status": "ok", "deck": {"code": wild}})
    monkeypatch.setattr(bridge, "service", lambda _: NS(evaluate_code=evaluate))
    await bridge.query(p, e, action="analyze", mode="wild", deck_index=1)
    evaluate.assert_awaited_once_with(wild, "wild")


def test_model_payload_keeps_every_card_and_rune_requirement():
    deck_code, cards = dk_deck()
    original = analyze(deck_code, cards)
    compact = bridge.model_data(original)
    assert compact["code"] == deck_code
    assert len(compact["cards"]) == 15
    assert sum(c["count"] for c in compact["cards"]) == 30
    assert next(c for c in compact["cards"] if c["dbf_id"] == 100)["runes"]["blood"] == 2


async def test_user_urls_never_become_network_targets(monkeypatch):
    s = NS(deck=AsyncMock(), yingdi_detail=AsyncMock(), lookup=AsyncMock())
    monkeypatch.setattr(bridge, "service", lambda _: s)
    result = await bridge.query(plugin(), event("x"), action="analyze", deck="http://127.0.0.1/private")
    assert result["status"] == "missing_deck"
    s.yingdi_detail.assert_not_awaited()
    result = await bridge.query(plugin(), event("x"), action="card", query="https://example.com/?token=private")
    assert result["status"] == "invalid_or_unavailable"
    s.lookup.assert_not_awaited()


async def test_strength_query_passes_named_family_in_both_modes(monkeypatch):
    s = NS(recommend=AsyncMock(return_value={"status": "ok", "decks": []}))
    monkeypatch.setattr(bridge, "service", lambda _: s)
    result = await bridge.query(plugin(), event("海盗战强不强"), action="evaluate", query="海盗战")
    assert len(result["results"]) == 2
    assert {c.args[0] for c in s.recommend.await_args_list} == {"standard", "wild"}
    assert all(c.kwargs["archetype_query"] == "海盗战" for c in s.recommend.await_args_list)


async def test_replacement_keeps_catalog_unmodified():
    deck_code, cards = dk_deck()
    before = deepcopy(cards)
    replacement_candidates(analyze(deck_code, cards), "测试牌110", cards)
    assert cards == before
