"""User-visible recommendations, whole-match coverage and lossless QQ delivery."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.mahjong.ai_coach import CoachRun, Comparison, NodeCoaching
from app.services.mahjong.highlights import (
    action_label,
    decision_digest,
    readable_tiles,
    select_decisions,
    situation,
    split_report,
)
from app.services.mahjong.records import parse_record
from app.services.mahjong.review import review, summary


@pytest.fixture(scope="module")
def reviewed():
    path = Path(__file__).parent / "fixtures/mahjong/upstream/rinshan.json"
    return review(parse_record(path.read_bytes(), match="半庄"), 3)


def test_selection_reaches_late_rounds_and_repeat_honba(reviewed):
    first = reviewed.analysis.decisions[0]
    decisions = tuple(replace(first, view=replace(first.view, round=r, honba=h, event=e))
                      for r, h in [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1), (7, 0)]
                      for e in range(1, 12))
    selected = select_decisions(decisions)
    assert {(d.view.round, d.view.honba) for _, d in selected} == {
        (0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1), (7, 0)}
    assert len(selected) == 7 and selected[-1][1].view.round == 7


def test_round_representative_prefers_real_discard_to_pass(reviewed):
    choices = reviewed.analysis.decisions
    selected = select_decisions(choices, minimum=1)
    assert any(d.actual.startswith(("discard:", "riichi:")) and len(d.candidates) > 1 for d in choices)
    assert selected[0][1].actual.startswith(("discard:", "riichi:"))


def test_default_reply_contains_actual_preferred_retained_tiles_and_scene(reviewed):
    first = reviewed.analysis.decisions[0]
    actual = next(c for c in first.candidates if c.id == first.actual)
    preferred = next(c for c in first.candidates if c.id.startswith("discard:") and c.id != actual.id)
    preferred_index = first.candidates.index(preferred) + 1
    actual_index = first.candidates.index(actual) + 1
    coaching = CoachRun("complete", (NodeCoaching(
        1, "ok", (Comparison(preferred_index, "保留发展", "退路减少", "他家立直时重新比较"),
                  Comparison(actual_index, "保留退路", "牌形发展受损", "进攻不足时重评")),
        preferred_index, "保留组合发展", "对手进度未知", "当前需要追分", "先争取改良，遇威胁再收缩"),),
        1, len(reviewed.analysis.decisions), "gpt-6-luna")
    result = replace(reviewed, coaching=coaching)
    text = summary(result, "test")
    # The normal reply uses representative nodes; directly check the exact node too.
    digest = decision_digest(first, 1, coaching)
    assert action_label(actual) in digest and action_label(preferred) in digest
    assert "保留" in digest and "本次不优先" in digest and "实战这手的代价：牌形发展受损" in digest
    assert "当前需要追分" in digest and "攻守计划：" in digest
    assert "场况：" in text and "实际：" in text and "牌效对照：" in text


def test_local_fallback_keeps_specific_actions_without_claiming_luna(reviewed):
    text = decision_digest(reviewed.analysis.decisions[0], 1)
    assert "牌效候选（非攻守定论）" in text and "实际：" in text
    assert "GPT-6 Luna 建议" not in text
    assert readable_tiles("打0m、5p、7s、1z、7z") == "打赤5万、5筒、7索、东、中"


def test_situation_uses_this_nodes_scores_and_public_threats(reviewed):
    d = reviewed.analysis.decisions[0]
    view = replace(d.view, seat=2, dealer=1, scores=(27000, 34000, 14000, 25000),
                   riichi_at=(None, 3, None, None))
    text = situation(replace(d, view=view))
    assert "本人14000点" in text and "第4" in text and "距上一名11000点" in text
    assert "东家（庄）已立直" in text


def test_report_splitting_keeps_every_character():
    text = ("场况与切牌建议\n" * 51 + "\n") * 12
    parts = split_report(text)
    assert len(parts) > 1 and all(len(part) <= 1800 for part in parts)
    assert "".join(parts) == text


async def test_native_hook_delivers_every_report_part(monkeypatch):
    from test_astrbot_plugin import FakeEvent, _load_plugin

    module = _load_plugin(monkeypatch)
    plugin = module.QQChatPlugin(SimpleNamespace(), {"legacy_chat": False})
    plugin.runtime = SimpleNamespace(_core=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))
    report = "场况与切牌建议\n" * 700

    async def fake_review(*args):
        return report

    monkeypatch.setattr("app.astrbot_mahjong.handle_native_review", fake_review)
    event = FakeEvent({"message_type": "private", "self_id": "bot", "user_id": "human", "sender": {}})
    event.get_platform_name = lambda: "aiocqhttp"
    event.get_message_str = lambda: "雀魂分析 帮助"
    event.get_extra = lambda *args: ""
    event.make_result = lambda: SimpleNamespace(message=lambda text: text)
    event.send = AsyncMock()
    await plugin.on_mahjong_review(event)
    replies = [call.args[0] for call in event.send.call_args_list]
    assert len(replies) > 1 and "".join(replies) == report
    assert event.llm_calls == [False]
