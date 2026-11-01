from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.latest_comic_source import (
    LatestComicSourceError,
    comic_source_time_note,
    extract_latest_comic_topic,
    parse_latest_comic_topic,
    select_recent_comic_messages,
)

NOW = 1_792_990_000


def message(text, age=0, sender="human", mid=None):
    return SimpleNamespace(text_content=text, timestamp=NOW - age, sender_id=sender,
                           message_id=mid or f"{age}-{text}")


def records():
    return select_recent_comic_messages([
        message("4090现在这个报价好高", 120), message("二手卡也涨了，5090呢", 50),
        message("5090D解锁其实只是变快", 5),
    ], now=NOW)


def result(**updates):
    data = {"topic": "显卡价格和性能", "detail": "群友讨论4090和5090D的报价与性能，报价未核验。",
            "evidence_indices": [1, 2, 3]}
    return json.dumps({**data, **updates}, ensure_ascii=False)


def test_old_messages_bots_commands_and_acknowledgements_never_fill_a_recent_window():
    selected = select_recent_comic_messages([
        message("昨天在讨论LED焊接", 3600), message("旧话题正好超时", 901),
        message("机器人自己的旧回复", 20, sender="bot"), message("群漫画", 1),
        message("请生成最新群漫画", 2), message("/群分析", 3), message("哈哈", 4),
        message("未来时间的错误消息", -5), message("现在讨论5090显卡", 5),
    ], now=NOW, bot_ids=["bot"])
    assert [row["text"] for row in selected] == ["现在讨论5090显卡"]
    assert select_recent_comic_messages([message("旧维修话题", 86400)], now=NOW) == []


def test_sorting_and_cap_keep_the_newest_forty_not_the_oldest_forty():
    selected = select_recent_comic_messages(
        [message(f"真人讨论第{index}条", index) for index in range(70)], now=NOW,
    )
    assert len(selected) == 40
    assert selected[0]["text"] == "真人讨论第39条" and selected[-1]["text"] == "真人讨论第0条"
    assert selected[-1]["index"] == 40


def test_latest_topic_requires_latest_evidence_and_keeps_actual_time_range():
    topic = parse_latest_comic_topic(result(), records(), selected_at=NOW)
    assert topic["topic"] == "显卡价格和性能"
    assert topic["source_window"]["start_ts"] == NOW - 120
    assert topic["source_window"]["end_ts"] == NOW - 5
    assert "最近15分钟" in comic_source_time_note(topic["source_window"])


@pytest.mark.parametrize("updates", [
    {"evidence_indices": [1, 2]}, {"evidence_indices": [1, 2, 4]},
    {"evidence_indices": [True, 3]}, {"detail": "5090价格88888元", "evidence_indices": [1, 2, 3]},
    {"topic": "电子维修", "detail": "群友讨论LED灯珠焊接", "evidence_indices": [1, 2, 3]},
])
def test_stale_unrelated_and_invented_evidence_is_rejected(updates):
    with pytest.raises(LatestComicSourceError):
        parse_latest_comic_topic(result(**updates), records(), selected_at=NOW)


async def test_latest_extractor_is_one_clean_bounded_call_without_old_history():
    once = AsyncMock(return_value=SimpleNamespace(completion_text=result()))
    context = SimpleNamespace(get_provider_by_id=lambda name: SimpleNamespace(qqchat_repair_once=once))
    topic = await extract_latest_comic_topic(context, records(), "free-analysis", selected_at=NOW)
    assert topic["topic"] == "显卡价格和性能" and once.await_count == 1
    kwargs = once.await_args.kwargs
    assert "5090D" in kwargs["prompt"] and "LED" not in kwargs["prompt"]
    assert kwargs["deadline_seconds"] == 12


async def test_insufficient_recent_material_never_calls_an_api():
    lookup = AsyncMock()
    with pytest.raises(LatestComicSourceError):
        await extract_latest_comic_topic(SimpleNamespace(get_provider_by_id=lookup), records()[:1], "free")
    assert not lookup.called


def test_missing_or_invalid_time_window_cannot_be_labeled_latest():
    assert comic_source_time_note({}) == ""
    assert comic_source_time_note({"mode": "latest", "start_ts": 0, "end_ts": NOW, "selected_at": NOW}) == ""
