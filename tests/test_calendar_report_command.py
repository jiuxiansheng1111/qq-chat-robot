from __future__ import annotations

import sys
import types
import ast
import inspect
import typing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from test_astrbot_plugin import _load_plugin


def setup(monkeypatch, *, allowed=True):
    module = _load_plugin(monkeypatch)
    plugin = module.QQChatPlugin(object(), {"legacy_chat": False})
    plugin.runtime = object()
    plugin._group_router = SimpleNamespace(in_scope=lambda event: True, ignored_sender=lambda event: False)
    plugin._legacy_event_allowed = AsyncMock(return_value=allowed)
    components = types.ModuleType("astrbot.api.message_components")
    components.Plain = lambda text: ("text", text)
    components.Image = SimpleNamespace(fromFileSystem=lambda path: ("image", path))
    monkeypatch.setitem(sys.modules, components.__name__, components)
    calls = []
    event = SimpleNamespace(
        message_obj=SimpleNamespace(raw_message={"post_type": "message", "message_type": "group",
            "group_id": "1000000003", "user_id": "member", "self_id": "bot",
            "sender": {"role": "member"}}),
        should_call_llm=lambda value: calls.append(("llm", value)),
        stop_event=lambda: calls.append(("stop", True)),
        plain_result=lambda text: text, chain_result=lambda chain: chain,
        send=AsyncMock(),
    )
    return plugin, event, calls


async def test_ordinary_member_gets_yesterday_and_today_in_one_labeled_final_chain(monkeypatch):
    plugin, event, calls = setup(monkeypatch)
    request = AsyncMock(return_value=SimpleNamespace(items=[
        SimpleNamespace(header="昨日群报｜2026-10-25", text="", image_path="/reports/yesterday.png"),
        SimpleNamespace(header="今日群报｜截至12:33:59", text="", image_path="/reports/today.png"),
    ]))
    monkeypatch.setattr("app.astrbot_group_reports.request_group_reports", request)
    await plugin.calendar_group_reports(event)
    results = [call.args[0] for call in event.send.await_args_list]
    assert len(results) == 2
    assert results[1] == [("text", "昨日群报｜2026-10-25\n"), ("image", "/reports/yesterday.png"),
                          ("text", "今日群报｜截至12:33:59\n"), ("image", "/reports/today.png")]
    assert calls == [("llm", False), ("stop", True)]
    request.assert_awaited_once_with(plugin.context, event)


async def test_group_acl_denial_does_not_fetch_or_generate_reports(monkeypatch):
    plugin, event, _ = setup(monkeypatch, allowed=False)
    request = AsyncMock()
    monkeypatch.setattr("app.astrbot_group_reports.request_group_reports", request)
    await plugin.calendar_group_reports(event)
    results = [call.args[0] for call in event.send.await_args_list]
    assert len(results) == 1 and "已启用" in results[0]
    assert not request.called


async def test_single_day_command_passes_date_and_sends_only_requested_report(monkeypatch):
    plugin, event, calls = setup(monkeypatch)
    request = AsyncMock(return_value=SimpleNamespace(items=[
        SimpleNamespace(header="昨日群报｜2026-10-27", text="", image_path="/reports/27.png"),
    ]))
    monkeypatch.setattr("app.astrbot_group_reports.request_group_reports", request)
    await plugin.calendar_group_reports(event, "2026-10-27")
    results = [call.args[0] for call in event.send.await_args_list]
    assert len(results) == 2
    assert results[1] == [("text", "昨日群报｜2026-10-27\n"), ("image", "/reports/27.png")]
    request.assert_awaited_once_with(plugin.context, event, date_text="2026-10-27")
    assert calls == [("llm", False), ("stop", True)]


async def test_native_handler_stopping_pipeline_still_finishes_owned_delivery(monkeypatch):
    # Exercise the installed AstrBot handler wrapper: the scheduler stops at its
    # first yield, so a command that itself yields early would lose the report.
    source = Path(__file__).resolve().parents[1] / "data/astrbot/runtime/tool-envs/astrbot/Lib/site-packages/astrbot/core/pipeline/context_utils.py"
    if not source.is_file():
        pytest.skip("local AstrBot runtime is not installed")
    node = next(item for item in ast.parse(source.read_text(encoding="utf-8")).body
                if isinstance(item, ast.AsyncFunctionDef) and item.name == "call_handler")
    namespace = {"inspect": inspect, "T": typing, "AstrMessageEvent": object,
                 "MessageEventResult": type("MessageEventResult", (), {}),
                 "CommandResult": type("CommandResult", (), {}), "logger": MagicMock()}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    plugin, event, calls = setup(monkeypatch)
    event.is_stopped = lambda: ("stop", True) in calls
    request = AsyncMock(return_value=SimpleNamespace(items=[
        SimpleNamespace(header="昨日群报｜2026-10-27", text="", image_path="/reports/27.png"),
    ]))
    monkeypatch.setattr("app.astrbot_group_reports.request_group_reports", request)
    handler = namespace["call_handler"](event, plugin.calendar_group_reports, "昨天")
    async for _ in handler:
        if event.is_stopped():
            break
    await handler.aclose()
    request.assert_awaited_once()
    assert event.send.await_count == 2
    assert event.send.await_args.args[0][-1] == ("image", "/reports/27.png")
