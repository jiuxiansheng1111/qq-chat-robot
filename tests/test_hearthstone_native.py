"""Real AstrBot types and tool registration; all QQ sends remain intercepted."""
import importlib.util
import json
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize("private", [False, True])
async def test_native_lookup_tool_and_context(monkeypatch, tmp_path, private):
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
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.utils.metrics import Metric

    from app import astrbot_hearthstone as bridge
    from app.config import Settings

    monkeypatch.setattr(Metric, "upload", AsyncMock())
    spec = importlib.util.spec_from_file_location("_qq_hearthstone_main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    plugin = module.QQChatPlugin(NS(get_all_stars=list), {
        "legacy_chat": False, "group_chat_groups": ["12345"], "natural_commands_enabled": True,
    })
    plugin.runtime = NS(_core=NS(settings=Settings(_env_file=None)))
    monkeypatch.setattr(plugin, "_legacy_event_allowed", AsyncMock(return_value=True))
    lookup = AsyncMock(return_value={"status": "ok", "cards": [{"name": "火球术", "mana": 4, "effect": "造成6点伤害。"}]})
    monkeypatch.setattr(bridge, "service", lambda _: NS(lookup=lookup))
    event, api, raw = _make_group_event(types)
    event.message_str = "查卡 标准 火球术"
    raw["message"] = [{"type": "text", "data": {"text": "/查卡 标准 火球术"}}]
    event.message_obj.message = [types.Plain(event.message_str)]
    event.is_at_or_wake_command = True
    if private:
        event.message_obj.type = types.MessageType.FRIEND_MESSAGE
        event.message_obj.group_id = ""
        raw["message_type"] = "private"
        raw.pop("group_id", None)
    # Queries embedded in an LLM turn still use the read-only card tool.
    request = ProviderRequest(prompt=event.message_str, system_prompt="current persona")
    await bridge.prepare(plugin, event, request)
    assert lookup.await_args.args == ("火球术", "standard")
    assert event.get_extra("qqchat_hearthstone_prepared")
    assert "火球术" in str(request.extra_user_content_parts)
    tool = json.loads(await plugin.qqchat_hearthstone(event, action="card", query="火球术", mode="standard"))
    assert tool["status"] == "ok" and tool["cards"][0]["mana"] == 4
    assert api.calls == []


@pytest.mark.parametrize("private", [False, True])
async def test_native_card_preview_and_confirm_send_selected_printing(monkeypatch, tmp_path, private):
    import base64

    from test_astrbot_native_onebot import (
        _find_project_root,
        _load_astrbot_types,
        _make_group_event,
    )
    from test_hearthstone import card

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    types = _load_astrbot_types(root, monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    from astrbot.core.utils.metrics import Metric

    from app.config import Settings
    from app.services.hearthstone.service import HearthstoneService, utc_now

    monkeypatch.setattr(Metric, "upload", AsyncMock())
    spec = importlib.util.spec_from_file_location("_qq_card_selection_main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    plugin = module.QQChatPlugin(NS(get_all_stars=list), {
        "legacy_chat": False, "group_chat_groups": ["12345"], "natural_commands_enabled": True,
    })
    plugin.runtime = NS(_core=NS(settings=Settings(_env_file=None)))
    plugin._music_listening = NS(observe=lambda _: None)
    monkeypatch.setattr(plugin, "_legacy_event_allowed", AsyncMock(return_value=True))
    monkeypatch.setattr("app.astrbot_owner_memory.observe", AsyncMock())
    lookup = HearthstoneService(tmp_path)
    lookup.catalog_at = utc_now()
    lookup.catalog = {101: {**card(101, name="提里奥·弗丁", effect="新版效果"), "set": "核心系列"},
                      102: {**card(102, name="提里奥·弗丁", effect="旧版效果", standard=False), "set": "怀旧系列"}}
    image = b"mocked-selected-printing"
    lookup.card_image = AsyncMock(return_value=image)
    plugin._hearthstone_service = lookup

    async def route(text, message_id, *, who=12345, target=None):
        event, api, raw = _make_group_event(types)
        raw.update(user_id=who, message_id=message_id, message=[{"type": "text", "data": {"text": text}}])
        event.message_obj.sender.user_id = str(who)
        event.message_obj.message_id = str(message_id)
        event.message_obj.message = [types.Plain(text)]
        event.message_str = text
        if target:
            raw["message"].insert(0, {"type": "at", "data": {"qq": target}})
            event.message_obj.message.insert(0, types.At(qq=target))
        if private:
            event.message_obj.type = types.MessageType.FRIEND_MESSAGE
            event.message_obj.group_id = ""
            raw["message_type"] = "private"
            raw.pop("group_id", None)
        else:
            event.message_obj.group_id = str(raw["group_id"])
            plugin._get_group_router().prepare_event(event, ("查卡",))

        async def send_group(**params):
            return await api.call_action("send_group_msg", **params)

        async def send_private(**params):
            return await api.call_action("send_private_msg", **params)

        event.bot.send_group_msg, event.bot.send_private_msg = send_group, send_private
        await plugin.route_native_group_message(event)
        return event, api.calls

    e, preview = await route("查询卡牌提里奥弗丁", 1, target=99999)
    assert e.is_stopped() and not e.call_llm and len(preview) == 1
    segments = preview[0][1]["message"]
    assert [s["type"] for s in segments] == ["text"]
    text = segments[0]["data"]["text"]
    assert "核心系列" in text and "怀旧系列" in text and "新版效果" in text and "旧版效果" in text
    lookup.card_image.assert_not_awaited()
    _, other = await route("2", 2, who=76543)
    assert not other
    if not private:
        _, misdirected = await route("2", 3, target=76543)
        assert not misdirected
    _, unclear = await route("确认", 4)
    assert "序号" in str(unclear) and not lookup.card_image.await_count
    e, sent = await route("第2张", 5)
    assert e.is_stopped() and not e.call_llm and len(sent) == 1
    action, params = sent[0]
    assert action == ("send_private_msg" if private else "send_group_msg")
    details, art = params["message"]
    assert "旧版效果" in details["data"]["text"] and "怀旧系列" in details["data"]["text"]
    assert "构筑模式：狂野" in details["data"]["text"] and "新版效果" not in details["data"]["text"]
    assert art["type"] == "image" and base64.b64decode(art["data"]["file"].removeprefix("base64://")) == image
    assert lookup.card_image.await_args.args[0]["dbf_id"] == 102


@pytest.mark.parametrize("private", [False, True])
async def test_native_plain_reply_has_no_format_stars_before_voice_and_send(monkeypatch, tmp_path, private):
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
    spec = importlib.util.spec_from_file_location("_qq_plain_stars_main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    plugin = module.QQChatPlugin(NS(get_all_stars=list), {"legacy_chat": False})
    plugin.runtime = NS()
    e, api, raw = _make_group_event(types)
    if private:
        e.message_obj.type = types.MessageType.FRIEND_MESSAGE
        e.message_obj.group_id = ""
        raw["message_type"] = "private"
        raw.pop("group_id", None)
    e.set_result(e.plain_result("**20牧**自带20张\n* *资源型*卡组\n代码 `2**3`"))
    observed = []

    async def voice(_, current):
        observed.append(current.get_result().get_plain_text())

    async def intercept(**params):
        return await api.call_action("send_private_msg" if private else "send_group_msg", **params)

    monkeypatch.setattr("app.astrbot_chat.decorate_native_voice", voice)
    e.bot.send_group_msg = e.bot.send_private_msg = intercept
    await plugin.filter_qq_source_links(e)
    assert observed == ["20牧自带20张\n• 资源型卡组\n代码 `2**3`"]
    await e.send(e.get_result())
    assert len(api.calls) == 1
    sent = "".join(p["data"]["text"] for p in api.calls[0][1]["message"] if p["type"] == "text")
    assert sent == observed[0]


def test_non_qq_request_removes_only_its_copy_of_hearthstone_tool(monkeypatch, tmp_path):
    from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    _load_astrbot_types(root, monkeypatch)
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    from astrbot.core.provider.entities import ProviderRequest

    from app.astrbot_hearthstone import scoped_tools

    original = ToolSet(tools=[FunctionTool(name=n, description="tool", parameters={})
                              for n in ("qqchat_hearthstone", "qqchat_arena", "another_tool")])
    request = ProviderRequest(prompt="原来的B站聊天", system_prompt="原人格", func_tool=original)
    scoped_tools(NS(get_platform_name=lambda: "bilibili"), request)
    assert request.system_prompt == "原人格"
    assert request.func_tool.get_tool("qqchat_hearthstone") is None
    assert request.func_tool.get_tool("qqchat_arena") is None
    assert original.get_tool("qqchat_hearthstone") is not None
    assert original.get_tool("qqchat_arena") is not None
    assert request.func_tool.get_tool("another_tool") is original.get_tool("another_tool")


@pytest.mark.parametrize("private", [False, True])
async def test_native_arena_command_tool_and_temporary_data(monkeypatch, tmp_path, private):
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
    from astrbot.core.provider.entities import ProviderRequest
    from astrbot.core.utils.metrics import Metric

    from app import astrbot_arena, astrbot_hearthstone
    from app.config import Settings

    monkeypatch.setattr(Metric, "upload", AsyncMock())
    spec = importlib.util.spec_from_file_location("_qq_arena_main", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    plugin = module.QQChatPlugin(NS(get_all_stars=list), {
        "legacy_chat": False, "group_chat_groups": ["12345"], "natural_commands_enabled": True,
    })
    plugin.runtime = NS(_core=NS(settings=Settings(_env_file=None)))
    monkeypatch.setattr(plugin, "_legacy_event_allowed", AsyncMock(return_value=True))
    compare = AsyncMock(return_value={"status": "ok", "classes": ["Mage"],
        "options": [{"cards": [{"name": name, "heartharena_scores": {"Mage": score}}]}
                    for name, score in (("火球术", 80), ("烈焰风暴", 88), ("奥术智慧", 70))],
        "draft": {"picked": 0}})
    monkeypatch.setattr(astrbot_arena, "service", lambda _: NS(compare=compare))
    e, api, raw = _make_group_event(types)
    e.message_str = "竞技场选牌 法师 火球术|烈焰风暴|奥术智慧"
    raw["message"] = [{"type": "text", "data": {"text": "/" + e.message_str}}]
    e.message_obj.message = [types.Plain(e.message_str)]
    e.is_at_or_wake_command = False
    if private:
        e.message_obj.type = types.MessageType.FRIEND_MESSAGE
        e.message_obj.group_id = ""
        raw["message_type"] = "private"
        raw.pop("group_id", None)
    await plugin.arena_command(e)
    assert e.call_llm and e.get_extra("qqchat_skip_local_command")
    req = ProviderRequest(prompt=e.message_str, system_prompt="original persona")
    await astrbot_hearthstone.prepare(plugin, e, req)
    compare.assert_awaited_once_with([["火球术"], ["烈焰风暴"], ["奥术智慧"]], classes=["Mage"], drafted=[])
    assert req.extra_user_content_parts and "heartharena_scores" in str(req.extra_user_content_parts)
    response = json.loads(await plugin.qqchat_arena(e, choices="火球术|烈焰风暴|奥术智慧", player_class="法师"))
    assert response["status"] == "ok" and response["draft"]["picked"] == 0
    assert api.calls == []
