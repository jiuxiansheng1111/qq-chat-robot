import json
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from test_hearthstone import card, event, plugin

from app import astrbot_arena as bridge
from app import astrbot_hearthstone as constructed
from app.services.hearthstone.arena import ArenaService, RatingsParser, parse_options
from app.services.hearthstone.service import utc_now
from app.services.qq_feature_menu import core_groups, requested_topic, select_text_groups


@pytest.fixture
def data(tmp_path):
    cards = [card(1, name="火球术", mana=4, faction="Mage", effect="造成6点伤害。"),
             card(2, name="烈焰风暴", mana=7, faction="Mage", effect="对所有敌方随从造成5点伤害。"),
             card(3, name="奥术智慧", mana=3, faction="Mage", effect="抽两张牌。"),
             card(4, name="中立牌", mana=2, faction="Neutral", standard=False),
             card(5, name="三血牌", mana=5, runes=(3, 0, 0)),
             card(6, name="一冰牌", mana=1, runes=(0, 1, 0)),
             card(7, name="盗贼牌", mana=2, faction="Rogue")]
    hs = NS(cache_dir=tmp_path, catalog={c["dbf_id"]: c for c in cards},
            ensure_catalog=AsyncMock(), catalog_source=lambda: {"name": "test catalog"}, api=AsyncMock())
    s = ArenaService(hs)
    s.ratings = AsyncMock(return_value={"ratings": {"Mage": {"火球术": 80, "烈焰风暴": 88,
        "奥术智慧": 70, "中立牌": 62}, "Rogue": {"中立牌": 76, "盗贼牌": 60}},
        "fetched_at": utc_now(), "last_changelog": "2026-10-13"})
    s.class_statistics = AsyncMock(return_value={"classes": [], "scope": "not card winrate"})
    return s


def test_rating_parser_keeps_class_specific_neutral_scores_and_ignores_changelog():
    p = RatingsParser()
    p.feed('''<section class="tab tierlist mage" id="mage"><dl><dt class="neutral higher">中立牌
    <span>新</span></dt><dd class="score score_62">62</dd></dl></section>
    <section class="tab tierlist rogue" id="rogue"><dl><dt>中立牌</dt><dd class="score">76</dd></dl></section>
    <section id="changelog"><dt>中立牌</dt><dd class="score">999</dd></section>''')
    assert p.ratings == {"Mage": {"中立牌": 62}, "Rogue": {"中立牌": 76}}


async def test_rating_cache_has_distinct_fetch_and_update_dates(monkeypatch, tmp_path):
    html = '<h3>2026/10/13: update</h3>'
    for cls in ("mage", "rogue", "druid", "hunter", "paladin", "priest", "shaman", "warlock", "warrior", "death-knight", "demon-hunter"):
        html += f'<section class="tab tierlist" id="{cls}">' + ''.join(
            f'<dt>牌{i}</dt><dd class="score">80</dd>' for i in range(50)) + '</section>'
    from app.services.hearthstone import arena
    fetch = AsyncMock(return_value=html.encode())
    monkeypatch.setattr(arena, "request_bytes", fetch)
    s = ArenaService(NS(cache_dir=tmp_path))
    result = await s.ratings()
    assert result["last_changelog"] == "2026-10-13" and len(result["ratings"]) == 11
    assert json.loads((tmp_path / "arena-ratings.json").read_text(encoding="utf-8")) == result
    await s.ratings()
    fetch.assert_awaited_once()


async def test_offline_ratings_are_a_gap_not_a_zero_score(monkeypatch, tmp_path):
    from app.services.hearthstone import arena
    monkeypatch.setattr(arena, "request_bytes", AsyncMock(side_effect=httpx.ConnectError("offline")))
    result = await ArenaService(NS(cache_dir=tmp_path)).ratings()
    assert result["unavailable"] and result["ratings"] == {}


async def test_real_effect_curve_and_repeated_arena_cards_without_constructed_limits(data):
    result = await data.compare([["火球术"], ["烈焰风暴"], ["奥术智慧"]], classes=["Mage"],
                                drafted=["火球术"] * 3 + ["中立牌"])
    assert result["status"] == "ok" and result["draft"]["mana_curve"] == {"4": 3, "2": 1}
    first = result["options"][0]["cards"][0]
    assert first["heartharena_scores"] == {"Mage": 80} and first["already_picked"] == 3
    compact = constructed.model_data(result)
    assert compact["options"][0]["cards"][0]["heartharena_scores"] == {"Mage": 80}
    assert compact["draft"]["cards"][0]["count"] == 3


async def test_buckets_dual_class_and_runes_are_not_flattened(data):
    result = await data.compare([["火球术", "中立牌"], ["盗贼牌"]], classes=["Mage", "Rogue"])
    assert len(result["options"][0]["cards"]) == 2
    assert result["options"][0]["cards"][1]["heartharena_scores"] == {"Mage": 62, "Rogue": 76}
    assert not any(o["class_mismatch"] for o in result["options"])
    dk = await data.compare([["一冰牌"], ["三血牌"]], classes=["Deathknight"], drafted=["三血牌"] * 3)
    assert not dk["options"][0]["runes_after_pick"]["valid"]
    assert dk["options"][1]["runes_after_pick"]["used_slots"] == 3


async def test_names_and_class_mismatches_are_visible_not_guessed(data):
    unknown = await data.compare([["看不清的牌"]])
    assert unknown["status"] == "unrecognized_cards"
    data.ratings.assert_not_awaited()
    result = await data.compare([["火球术"]], classes=["Rogue"])
    assert result["options"][0]["class_mismatch"] == ["火球术"]
    result = await data.compare([["中立牌"]])
    assert result["class_confirmation_needed"]


async def test_class_stats_label_draft_samples_and_never_card_winrate(tmp_path):
    payload = {"meta": {"fetched_at": utc_now(), "stale": False}, "data": [
        {"class": "Mage", "win_rate": 52.2, "num_drafts": 1500, "pick_rate": 70.0,
         "firecrawl_credits_used": 999, "url": "https://example.com/irrelevant"}]}
    s = ArenaService(NS(cache_dir=tmp_path, api=AsyncMock(return_value=payload)))
    result = await s.class_statistics(["Mage"])
    assert result["classes"] == [{"class": "Mage", "winrate": 52.2, "draft_samples": 1500,
                                  "url": "https://hsreplay.net/arena/mage/"}]
    assert "HSReplay" in result["source"]["provider"]
    payload["meta"]["stale"] = True
    assert (await s.class_statistics(["Mage"]))["status"] == "unavailable"


def test_text_options_copies_and_class_aliases():
    assert parse_options("火球术+中立牌|烈焰风暴|奥术智慧") == [["火球术", "中立牌"], ["烈焰风暴"], ["奥术智慧"]]
    assert bridge.parse_cards("火球术x3|中立牌") == ["火球术"] * 3 + ["中立牌"]
    assert bridge.parse_classes("法师+盗贼") == ["Mage", "Rogue"]
    assert bridge.parse_classes("death knight") == ["Deathknight"]
    assert bridge.intent("竞技场开始 death knight")["player_class"] == "Deathknight"
    assert bridge.intent("竞技场选牌 法师 火球术|烈焰风暴|奥术智慧")["choices"] == "火球术|烈焰风暴|奥术智慧"


def test_rune_cost_overrides_legacy_rune_label_in_race_field():
    changed = card(8, name="拼接骑士", runes=(3, 0, 0))
    changed["tribe_or_school"] = "亡灵 血血"
    result = constructed.model_data(changed)
    assert result["runes"]["blood"] == 3 and result["tribe_or_school"] == "亡灵"
    assert changed["tribe_or_school"] == "亡灵 血血"


@pytest.mark.parametrize("text", ["竞技场帮助", "竞技场怎么用", "不要竞技场开始 法师", "他说竞技场选牌", "竞技场比赛谁赢了", "炉石卡组 标准",
                                  "竞技场开始后怎么选牌", "竞技场开始 怎么用"])
def test_help_negation_and_noncard_arena_chat_do_not_execute(text):
    assert bridge.intent(text) is None


async def test_start_explanation_cannot_clear_an_existing_draft(monkeypatch, data):
    monkeypatch.setattr(bridge, "service", lambda _: data)
    p = plugin()
    await bridge.query(p, event("竞技场已选 火球术"), action="add", cards="火球术")
    assert (await bridge.query(p, event("竞技场开始 怎么用"), action="start"))["status"] == "confirmation_needed"
    result = await bridge.query(p, event("竞技场牌组"), action="status")
    assert result["draft"]["picked"] == 1


async def test_readonly_pick_explicit_add_retry_undo_and_group_pick(monkeypatch, data):
    monkeypatch.setattr(bridge, "service", lambda _: data)
    p = plugin()
    await bridge.query(p, event("竞技场开始 法师"), action="start", player_class="法师")
    await bridge.query(p, event("竞技场选牌"), choices="火球术+中立牌|奥术智慧|烈焰风暴")
    status = await bridge.query(p, event("竞技场牌组"), action="status")
    assert status["draft"]["picked"] == 0
    assert (await bridge.query(p, event("哪张比较好"), action="add", cards="火球术"))["status"] == "confirmation_needed"
    e = event("竞技场已选 第1组")
    added = await bridge.query(p, e, action="add", choice_index=1)
    retry = await bridge.query(p, e, action="add", cards="火球术+中立牌")
    assert retry == added and added["draft"]["picked"] == 2
    assert added["added"] == ["火球术", "中立牌"]
    undone = await bridge.query(p, event("竞技场撤销"), action="undo")
    assert undone["draft"]["picked"] == 0


async def test_record_isolated_and_expires(monkeypatch, data):
    monkeypatch.setattr(bridge, "service", lambda _: data)
    p, e = plugin(), event("竞技场已选 火球术")
    await bridge.query(p, e, action="add", cards="火球术")
    for other in (event("竞技场牌组", user="other"), event("竞技场牌组", group="other")):
        result = await bridge.query(p, other, action="status")
        assert result["draft"]["picked"] == 0
    state = p._arena_drafts[constructed._scope(e)]
    state["at"] = time.monotonic() - 7 * 3600
    assert not bridge.has_draft(p, e)


async def test_invalid_or_oversize_records_do_not_partially_append(monkeypatch, data):
    monkeypatch.setattr(bridge, "service", lambda _: data)
    p = plugin()
    result = await bridge.query(p, event("竞技场已选 火球术x29"), action="add", cards="火球术x29")
    assert result["draft"]["picked"] == 29
    overflow = await bridge.query(p, event("竞技场已选 火球术x2"), action="add", cards="火球术x2")
    assert overflow["status"] == "invalid_or_unavailable"
    unknown = await bridge.query(p, event("竞技场已选 猜的卡"), action="add", cards="猜的卡")
    assert unknown["status"] == "unrecognized_cards"
    result = await bridge.query(p, event("竞技场牌组"), action="status")
    assert result["draft"]["picked"] == 29


async def test_new_unreadable_offer_does_not_reuse_previous_pick_order(monkeypatch, data):
    monkeypatch.setattr(bridge, "service", lambda _: data)
    p = plugin()
    await bridge.query(p, event("竞技场选牌"), choices="火球术|烈焰风暴|奥术智慧", player_class="法师")
    await bridge.query(p, event("下一轮这三张选哪个"))
    result = await bridge.query(p, event("已选第二张"), action="add", choice_index=2)
    assert result["status"] == "invalid_or_unavailable"
    assert not p._arena_drafts[constructed._scope(event("x"))]["picks"]


async def test_acl_platform_and_urls_before_network_or_state(monkeypatch, data):
    monkeypatch.setattr(bridge, "service", lambda _: data)
    p = plugin()
    p._legacy_event_allowed.return_value = False
    assert (await bridge.query(p, event("竞技场开始 法师"), action="start"))["status"] == "denied"
    assert not getattr(p, "_arena_drafts", {})
    p._legacy_event_allowed.return_value = True
    assert (await bridge.query(p, event("x", platform="bilibili")))["status"] == "unavailable"
    assert (await bridge.query(p, event("竞技场选牌"), choices="https://localhost/private"))["status"] == "invalid_or_unavailable"
    data.hs.ensure_catalog.assert_not_awaited()


async def test_screenshot_context_is_preserved_and_model_can_fetch_choices(monkeypatch, data):
    monkeypatch.setattr(bridge, "service", lambda _: data)
    p, e = plugin(), event("这三张选哪个好")
    caption = {"type": "text", "text": "<image_caption>炉石竞技场选牌，法师，从左到右：火球术、烈焰风暴、奥术智慧</image_caption>"}
    request = NS(system_prompt="原人格", func_tool=None, extra_user_content_parts=[caption])
    await constructed.prepare(p, e, request)
    await constructed.prepare(p, e, request)
    assert e.get_extra("qqchat_hearthstone_prepared")
    assert request.extra_user_content_parts[0] is caption
    assert request.system_prompt.count("【本轮炉石竞技场】") == 1
    data.ratings.assert_not_awaited()  # No name guessed from an unstructured image caption.
    result = await bridge.query(p, e, choices="火球术|烈焰风暴|奥术智慧", player_class="法师")
    assert result["options"][1]["cards"][0]["heartharena_scores"] == {"Mage": 88}


def test_help_explains_real_sources_without_draft_side_effects():
    images, groups = core_groups()
    result = select_text_groups(groups, "竞技场帮助")
    assert requested_topic("竞技场帮助") == ("竞技场选牌",)
    assert len(result) == 1 and "HearthArena" in str(result) and "竞技场撤销" in str(result)
    assert len(images) == 7
