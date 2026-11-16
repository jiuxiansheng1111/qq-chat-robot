from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_hearthstone import card, code, event, plugin

from app import astrbot_hearthstone as bridge
from app.services.hearthstone.aliases import is_twenty_priest, named_archetype
from app.services.hearthstone.decks import analyze, replacement_candidates
from app.services.hearthstone.service import HearthstoneService, archetype_matches, utc_now


def twenty_priest(mode=2):
    cards = {n: card(n, faction="Priest") for n in range(100, 110)}
    cards[126055] = {**card(126055, name="裂魂者阿扎莉娜", faction="Priest", rarity="传说",
        effect="你的初始生命值为40点。你的套牌为20张牌，外加20张敌方卡牌的复制。战吼：抽牌，直到达到你的手牌上限。"),
        "english_name": "Azalina Soulsever"}
    return code([(126055, 1), (100, 1)] + [(n, 2) for n in range(101, 110)], mode=mode), cards


@pytest.mark.parametrize("mode", [1, 2])
def test_twenty_list_is_complete_only_with_the_actual_enabler(mode):
    encoded, cards = twenty_priest(mode)
    deck = analyze(encoded, cards)
    assert deck["total_cards"] == 20 and HearthstoneService.usable(deck)
    assert deck["deck_size"]["starting_deck_cards"] == 40
    assert deck["deck_size"]["opponent_copies"] == 20
    assert sum(deck["mana_curve"].values()) == 20
    assert deck["dust_main"] == 800  # Only the actual submitted cards, not copied opponents.
    cards[126055]["effect"] = "旧版普通战吼。"
    assert not HearthstoneService.usable(analyze(encoded, cards))


def test_unrelated_twenty_list_and_wrong_print_are_rejected():
    encoded, cards = twenty_priest()
    new = dict(cards)
    new[500] = card(500, name="窃魂者阿扎莉娜", faction="Neutral", effect=cards[126055]["effect"])
    del new[126055]
    encoded = code([(500, 1), (100, 1)] + [(n, 2) for n in range(101, 110)])
    assert not HearthstoneService.usable(analyze(encoded, new))


def test_replacement_must_keep_twenty_card_enabler():
    encoded, cards = twenty_priest()
    deck = analyze(encoded, cards)
    assert not replacement_candidates(deck, "裂魂者阿扎莉娜", cards)["candidates"]
    cards[201] = card(201, faction="Priest")
    assert replacement_candidates(deck, "测试牌105", cards)["candidates"]


@pytest.mark.parametrize("text,action,mode,query", [
    ("推荐20牧", "recommend", "", "20牧"),
    ("炉石卡组 标准 20脏牧", "recommend", "standard", "20脏牧"),
    ("20牧强不强", "evaluate", "", "20牧"),
    ("来套狂野二十牧", "recommend", "wild", "二十牧"),
    ("宇宙20脏牧怎么样", "evaluate", "", "宇宙20脏牧"),
    ("20牧师", "recommend", "", "20牧师"),
    ("推荐Thief Priest", "recommend", "", "Thief Priest"),
])
def test_alias_routes_preserve_requested_family(text, action, mode, query):
    parsed = bridge.intent(text)
    assert parsed["action"] == action and parsed["mode"] == mode
    assert parsed["query"] == query and parsed["player_class"] == "Priest"


def test_alias_never_matches_other_priest_or_thief_classes():
    assert archetype_matches("Thief Priest", "20牧")
    assert archetype_matches("HL Thief Priest", "宇宙20脏牧")
    assert not archetype_matches("Thief Priest", "宇宙20脏牧")
    assert not archetype_matches("Shadow Priest", "20牧")
    assert not archetype_matches("Thief Rogue", "20牧")
    assert not is_twenty_priest("120牧") and named_archetype("120牧") == ""


async def test_recommend_tool_does_not_drop_named_query(monkeypatch):
    s = NS(recommend=AsyncMock(return_value={"status": "ok", "decks": []}))
    monkeypatch.setattr(bridge, "service", lambda _: s)
    await bridge.query(plugin(), event("推荐20牧"), **bridge.intent("推荐20牧"))
    assert s.recommend.await_count == 2
    assert all(c.kwargs["archetype_query"] == "20牧" and c.kwargs["player_class"] == "Priest"
               for c in s.recommend.await_args_list)


async def test_real_recommendation_path_accepts_verified_twenty_card_code(tmp_path):
    encoded, cards = twenty_priest()
    s = HearthstoneService(tmp_path)
    s.catalog, s.catalog_at = cards, utc_now()
    meta = {"fetched_at": utc_now(), "stale": False}
    family = {"archetype": "Thief Priest", "winrate": 50.1, "popularity": 10.5, "games": 5000}
    build = {"archetype": "Thief Priest", "class": "Priest", "format": "standard", "games": 1200,
        "deck_code": encoded, "updated_at": utc_now(), "win_rate": 52.5, "sample_period": "black_empire",
        "sample_rank": "legend", "url": "https://www.hsguru.com/deck/test"}
    s.api = AsyncMock(side_effect=[{"meta": meta, "data": {"format": "standard", "rank": "all",
        "period": "past_day", "items": [family]}}, {"meta": meta, "data": [build]}])
    result = await s.recommend("standard", archetype_query="20牧")
    assert result["status"] == "ok" and len(result["decks"]) == 1
    selected = result["decks"][0]
    assert selected["deck"]["total_cards"] == 20 and selected["display_name"] == "20脏牧"
    assert selected["archetype_stats"]["winrate"] == 50.1 and selected["build_stats"]["winrate"] == 52.5


async def test_community_sort_keeps_alias_filter(tmp_path):
    s = HearthstoneService(tmp_path)
    s.yingdi_decks = AsyncMock(return_value={"status": "ok"})
    await s.recommend("wild", sort="community", archetype_query="20牧")
    s.yingdi_decks.assert_awaited_once_with("wild", query="20牧", player_class="Priest", limit=3)
