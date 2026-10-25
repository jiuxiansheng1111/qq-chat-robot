"""Real application dispatch with mocked QQ output; not an online QQ E2E test."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.main import app, settings


def test_full_webhook_at_followup_attachment_permissions_and_duplicate(tmp_path, monkeypatch):
    import app.main as core
    import app.plugins.mahjong as plugin
    from app.services.mahjong import ai_coach as ai

    ai_calls = []

    class MockPinnedCoach:
        async def explain(self, messages):
            data = json.loads(messages[1]["content"])
            ai_calls.append(data)
            candidates = data["program_calculated_candidates"]
            return json.dumps({
                "comparisons": [{"candidate": c["candidate"], "benefit": "保留改良空间",
                                 "cost": "仍需支付推进风险", "change_when": "遇到立直重新评估"}
                                for c in candidates],
                "preferred": candidates[0]["candidate"], "reason": "兼顾速度与退路",
                "uncertainty": "对手进度无法确认",
            })

        async def aclose(self):
            pass

    monkeypatch.setattr(ai, "ZhipuCoach", lambda _: MockPinnedCoach())
    monkeypatch.setattr(settings, "llm_provider", "zhipu")
    monkeypatch.setattr(settings, "llm_base_url", "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.setattr(settings, "llm_api_key", "offline-test-key")
    monkeypatch.setattr(settings, "llm_model", "glm-4-flash-250414")

    monkeypatch.setattr(settings, "database_path", str(tmp_path / "review.db"))
    monkeypatch.setattr(settings, "onebot_api_base", "")
    monkeypatch.setattr(settings, "ingress_user_rate_limit_per_minute", 100)
    monkeypatch.setattr(settings, "ingress_group_rate_limit_per_minute", 100)
    monkeypatch.setattr(plugin, "_root", lambda _: tmp_path)
    output = AsyncMock()
    monkeypatch.setattr(core, "send_group_message", output)
    inbox = tmp_path / "data/mahjong/inbox"
    inbox.mkdir(parents=True)
    fixture = Path(__file__).parent / "fixtures/mahjong/upstream/rinshan.json"
    (inbox / "game.json").write_bytes(fixture.read_bytes())

    def event(message, identifier, user="u", group="g"):
        return {
            "post_type": "message",
            "message_type": "group",
            "self_id": "test-mahjong-bot",
            "group_id": group,
            "user_id": user,
            "message_id": identifier,
            "sender": {"role": "member", "user_id": user},
            "message": message,
        }

    headers = (
        {"X-OneBot-Token": settings.onebot_webhook_token} if settings.onebot_webhook_token else {}
    )
    with TestClient(app) as client:
        client.app.state.llm.ask = AsyncMock(return_value="普通聊天")
        activity = AsyncMock(wraps=client.app.state.db.record_group_activity)
        monkeypatch.setattr(client.app.state.db, "record_group_activity", activity)
        first = event(
            [
                {"type": "at", "data": {"qq": "test-mahjong-bot"}},
                {"type": "text", "data": {"text": "帮我分析一下这个对局 座位=北 赛制=半庄"}},
            ],
            "mahjong-request-1",
        )
        response = client.post("/onebot/webhook", json=first, headers=headers)
        assert response.json()["plugin"] == "mahjong_review"
        assert "还需要" in output.call_args.args[1]
        attachment = [
            {"type": "file", "data": {"name": "game.json", "url": "http://127.0.0.1/never-fetch"}}
        ]
        followup = event(attachment, "mahjong-attachment-1")
        assert (
            client.post("/onebot/webhook", json=followup, headers=headers).json()["plugin"]
            == "mahjong_review"
        )
        assert "10决策" in output.call_args.args[1]
        store = client.app.state.mahjong_reports
        assert len(store.jobs) == 1
        result = next(iter(store.jobs.values()))[2]
        assert result.coach_prompt_sha256 and result.coach_context
        assert result.coaching.completed == len(ai_calls) == ai.MAX_NODES
        assert "智谱AI已生成" in output.call_args.args[1]
        assert "本地主题教材" in output.call_args.args[1]
        assert client.app.state.llm.ask.await_count == 0
        assert activity.await_count == 0
        before = output.await_count
        assert (
            client.post("/onebot/webhook", json=followup, headers=headers).json()["reason"]
            == "duplicate_event"
        )
        assert output.await_count == before and len(store.jobs) == 1
        assert len(ai_calls) == ai.MAX_NODES
        client.portal.call(client.app.state.db.set_group_enabled, "disabled", False)
        disabled = event(
            "分析牌谱 座位=北 赛制=半庄 文件=game.json", "mahjong-disabled", group="disabled"
        )
        assert (
            client.post("/onebot/webhook", json=disabled, headers=headers).json()["reason"]
            == "group_disabled"
        )
        client.portal.call(client.app.state.db.set_plugin_enabled, "g", "mahjong_review", False)
        disabled_plugin = event("分析雀魂牌谱", "mahjong-plugin-disabled")
        assert (
            client.post("/onebot/webhook", json=disabled_plugin, headers=headers).json()["reason"]
            == "plugin_disabled"
        )
        assert len(store.jobs) == 1


def test_missing_coach_md_explicitly_degrades(monkeypatch):
    import app.services.mahjong.review as module
    from app.services.mahjong.records import RecordError, parse_record

    def missing():
        raise RecordError("专业教练MD不存在")

    monkeypatch.setattr(module, "load_coach_prompt", missing)
    fixture = Path(__file__).parent / "fixtures/mahjong/upstream/rinshan.json"
    result = module.review(parse_record(fixture.read_bytes(), match="半庄"), 3)
    assert result.coach_prompt_sha256 is None and not result.coach_context
    assert any("降级为纯规则计算" in w for w in result.warnings)
    assert "未加载" in module.summary(result, "job")
