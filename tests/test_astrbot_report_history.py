"""Exercise the installed OneBot paginator against a sparse NapCat UI cache."""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def adapter_type(monkeypatch, tmp_path):
    from test_astrbot_native_onebot import _load_astrbot_types

    root = Path(__file__).resolve().parents[1]
    plugin = root / "data/astrbot/instance/data/plugins/astrbot_plugin_qq_group_daily_analysis"
    if not plugin.is_dir():
        pytest.skip("installed daily plugin unavailable")
    _load_astrbot_types(root, monkeypatch)
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "astrbot"))
    monkeypatch.chdir(tmp_path)
    package = ModuleType("_history_pagination_contract")
    package.__path__ = [str(plugin)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    module = importlib.import_module(
        package.__name__ + ".src.infrastructure.platform.adapters.onebot_adapter"
    )
    return module.OneBotAdapter


def row(mid, when):
    return {"message_id": mid, "message_seq": mid, "time": when,
            "sender": {"user_id": 123, "nickname": "member"},
            "message": [{"type": "text", "data": {"text": str(mid)}}]}


@pytest.mark.asyncio
async def test_sparse_first_view_cannot_skip_intervening_messages(adapter_type):
    now = int(time.time())
    # The cache shows one ancient message plus the latest. IDs 2 and 3 are
    # missing from this view, but remain available through anchored history.
    bot = type("Bot", (), {})()
    bot.call_action = AsyncMock(side_effect=[
        {"app_name": "NapCat"},
        {"messages": [row(1, now - 100), row(4, now - 10)]},
        {"messages": [row(2, now - 80), row(3, now - 40), row(4, now - 10)]},
        {"messages": [row(1, now - 100), row(2, now - 80)]},
        {"messages": [row(1, now - 100)]},
    ])
    adapter = adapter_type(bot)
    result = await adapter.fetch_messages("1000000001", days=1, max_count=100)
    assert [item.message_id for item in result] == ["1", "2", "3", "4"]
    history_calls = [call.kwargs for call in bot.call_action.await_args_list
                     if call.args[0] == "get_group_msg_history"]
    assert history_calls[0].get("message_seq") is None
    assert history_calls[1]["message_seq"] == 4


@pytest.mark.asyncio
async def test_real_trigger_anchor_never_uses_first_view(adapter_type):
    now = int(time.time())
    bot = type("Bot", (), {})()
    bot.call_action = AsyncMock(side_effect=[
        {"app_name": "NapCat"},
        {"messages": [row(1, now - 100), row(2, now - 50)]},
        {"messages": [row(1, now - 100)]},
    ])
    result = await adapter_type(bot).fetch_messages(
        "1000000001", days=1, max_count=100, before_id="2"
    )
    assert [item.message_id for item in result] == ["1", "2"]
    calls = [call.kwargs for call in bot.call_action.await_args_list
             if call.args[0] == "get_group_msg_history"]
    assert calls[0]["message_seq"] == "2"


@pytest.mark.asyncio
async def test_standard_driver_keeps_its_original_pagination(adapter_type):
    now = int(time.time())
    bot = type("Bot", (), {})()
    bot.call_action = AsyncMock(side_effect=[
        {"app_name": "OtherOneBot"},
        {"messages": [row(1, now - 100), row(2, now - 50)]},
        {"messages": [row(1, now - 100)]},
    ])
    result = await adapter_type(bot).fetch_messages("1000000001", days=1, max_count=100)
    assert [item.message_id for item in result] == ["1", "2"]
    calls = [call.kwargs for call in bot.call_action.await_args_list
             if call.args[0] == "get_group_msg_history"]
    assert calls[1]["message_seq"] == 1
