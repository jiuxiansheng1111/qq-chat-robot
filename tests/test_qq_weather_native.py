"""The real native QQ pipeline handles the reported nickname weather request."""

import importlib.util
import json
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize("private", [False, True])
async def test_actual_qq_weather_entry_sends_one_report_and_skips_llm(
    monkeypatch, tmp_path, private
):
    from test_astrbot_native_onebot import (
        _find_project_root,
        _load_astrbot_types,
        _make_group_event,
    )

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    types = _load_astrbot_types(root, monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    from astrbot.core.utils.metrics import Metric

    monkeypatch.setattr(Metric, "upload", AsyncMock())
    from app.config import Settings
    from app.services.weather import WeatherReport

    spec = importlib.util.spec_from_file_location("_qq_weather_main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    plugin = module.QQChatPlugin(
        NS(get_all_stars=list),
        {
            "legacy_chat": False,
            "group_chat_groups": ["12345"],
            "natural_commands_enabled": True,
        },
    )
    plugin.runtime = NS(_core=NS(settings=Settings(_env_file=None)))
    monkeypatch.setattr(plugin, "_legacy_event_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr("app.astrbot_owner_memory.observe", AsyncMock())
    fetch = AsyncMock(
        return_value=WeatherReport("深圳", "Asia/Shanghai", 25.0, 26.0, 70.0, 8.0, 0.0, "多云", ())
    )
    monkeypatch.setattr("app.astrbot_weather.fetch_weather", fetch)
    event, api, raw = _make_group_event(types)
    text = "小肥鱼深圳今天天气怎么样"
    event.message_str = text
    event.message_obj.message = [types.Plain(text)]
    raw["message"] = [{"type": "text", "data": {"text": text}}]
    event.is_at_or_wake_command = False
    if private:
        event.message_obj.type = types.MessageType.FRIEND_MESSAGE
        event.message_obj.group_id = ""
        raw["message_type"] = "private"
        raw.pop("group_id", None)
        plugin._music_listening = NS(observe=lambda _: None)

    async def send_message(**params):
        return await api.call_action("send_private_msg" if private else "send_group_msg", **params)

    event.bot.send_group_msg = send_message
    event.bot.send_private_msg = send_message
    await plugin.route_native_group_message(event)
    assert event.is_stopped()
    assert len(api.calls) == 1 and "25°C" in str(api.calls[0])
    fetch.assert_awaited_once()
    assert fetch.await_args.args[0] == "深圳"
    assert event.call_llm is False
    result = json.loads(await plugin.qqchat_weather(event, "深圳"))
    assert result["status"] == "ok" and result["weather"]["temperature_c"] == 25
    assert len(api.calls) == 1  # A data tool does not send a second report.


def test_weather_tool_is_removed_only_from_other_platform_request(monkeypatch, tmp_path):
    from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    _load_astrbot_types(root, monkeypatch)
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    from astrbot.core.provider.entities import ProviderRequest

    from app.astrbot_weather import prepare

    original = ToolSet(
        tools=[
            FunctionTool(name=name, description="existing tool", parameters={})
            for name in ("qqchat_weather", "another_tool")
        ]
    )
    request = ProviderRequest(prompt="普通聊天", system_prompt="B站原文", func_tool=original)
    prepare(NS(get_platform_name=lambda: "bilibili"), request)
    assert request.system_prompt == "B站原文"
    assert request.func_tool.get_tool("qqchat_weather") is None
    assert request.func_tool.get_tool("another_tool") is original.get_tool("another_tool")
    assert original.get_tool("qqchat_weather") is not None
