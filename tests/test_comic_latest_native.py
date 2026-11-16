from __future__ import annotations

import importlib
import json
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@asynccontextmanager
async def slot(*args, **kwargs):
    yield


@pytest.fixture
def service(monkeypatch):
    pytest.importorskip("astrbot")
    import astrbot.api  # noqa: F401 -- 原生注册表需先于ProviderManager初始化。

    root = Path(__file__).resolve().parents[1]
    source = root / "data/astrbot/instance/data/plugins/astrbot_plugin_qq_group_daily_analysis/src"
    if not source.is_dir():
        pytest.skip("需要已安装的群分析插件以读取其实际分析服务")
    package = types.ModuleType("_native_latest_comic")
    package.__path__ = [str(source)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    module = importlib.import_module(package.__name__ + ".application.services.analysis_application_service")
    values = importlib.import_module(package.__name__ + ".domain.value_objects.unified_message")
    now = 1_792_990_000
    monkeypatch.setattr(module.time_mod, "time", lambda: now)

    def message(text, age, sender="human"):
        return values.UnifiedMessage(
            message_id=f"{age}-{sender}", sender_id=sender, sender_name="群友", group_id="1000000001",
            text_content=text, timestamp=now-age,
            contents=(values.MessageContent(type=values.MessageContentType.TEXT, text=text),),
        )

    records = [message("以前讨论LED灯珠焊接", 3600), message("4090价格好高", 100), message("5090二手卡也涨了", 5)]
    adapter = SimpleNamespace(platform_id="qq-local", is_group_muted=AsyncMock(return_value=False),
                              fetch_messages=AsyncMock(return_value=records))
    once = AsyncMock(return_value=SimpleNamespace(completion_text=json.dumps({
        "topic": "显卡价格", "detail": "群友在讨论4090和5090显卡涨价。", "evidence_indices": [1, 2],
    }, ensure_ascii=False)))
    instance = object.__new__(module.AnalysisApplicationService)
    instance.config_manager = SimpleNamespace(
        get_max_messages=lambda: 1000, get_bot_self_ids=lambda: ["bot"],
        get_filter_bot_messages=lambda: True, get_topic_provider_id=lambda: "free-analysis",
    )
    instance.bot_manager = SimpleNamespace(get_adapter=lambda platform: adapter)
    instance.group_lock = slot
    instance._llm_slot = slot
    instance.llm_analyzer = SimpleNamespace(
        context=SimpleNamespace(get_provider_by_id=lambda name: SimpleNamespace(qqchat_repair_once=once)),
        analyze_topics=AsyncMock(return_value=([{"topic": "历史"}], None)),
    )
    instance.statistics_service = SimpleNamespace(_convert_to_legacy_dict=lambda messages: messages)
    return instance, adapter, once, now, message


async def test_actual_service_default_requests_recent_window_and_never_analyzes_day(service):
    instance, adapter, once, now, _ = service
    result = await instance.execute_comic_topic_analysis("1000000001", "qq-local")
    assert result["success"] and len(result["topics"]) == 1
    assert result["topics"][0]["topic"] == "显卡价格"
    adapter.fetch_messages.assert_awaited_once_with(group_id="1000000001", days=1, max_count=120, since_ts=now-900)
    assert "LED" not in once.await_args.kwargs["prompt"]
    assert not instance.llm_analyzer.analyze_topics.called
    assert result["source_window"]["end_ts"] == now-5


async def test_actual_service_does_not_fill_empty_recent_period_with_old_messages(service):
    instance, adapter, once, _, message = service
    adapter.fetch_messages.return_value = [message("昨天在讨论维修电路板", 86400)]
    result = await instance.execute_comic_topic_analysis("1000000001", "qq-local")
    assert result["success"] is False and result["reason"] == "no_recent_topic"
    assert not once.called and not instance.llm_analyzer.analyze_topics.called


async def test_explicit_history_days_remains_a_distinct_opt_in(service):
    instance, adapter, once, _, _ = service
    result = await instance.execute_comic_topic_analysis("1000000001", "qq-local", days=3)
    assert result["success"] and not once.called
    adapter.fetch_messages.assert_awaited_once_with(group_id="1000000001", days=3, max_count=1000)
    instance.llm_analyzer.analyze_topics.assert_awaited_once()
