"""Real AstrBot events retain group speaker provenance and direct-only routing."""
import importlib.util
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types, _make_group_event


@pytest.mark.parametrize("addressed", [False, True])
async def test_actual_group_event_only_replies_when_addressed_and_keeps_native_quote_and_media(monkeypatch,tmp_path,addressed):
    root=_find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT",str(tmp_path/"isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    types=_load_astrbot_types(root,monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    from astrbot.api.message_components import Reply
    from astrbot.core.provider.entities import ProviderRequest

    spec=importlib.util.spec_from_file_location("_group_source_native_main",root/"main.py")
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module)
    spec.loader.exec_module(module)
    plugin=module.QQChatPlugin(NS(get_all_stars=list),{
        "legacy_chat":False,"group_chat_groups":["12345"],"natural_commands_enabled":True,
        "group_chat_unaddressed_questions":True,"group_chat_help_requests":True,
        "group_chat_trigger_keywords":["内存卡"],"group_chat_followup_seconds":120,
    })
    plugin.runtime=object()
    monkeypatch.setattr(plugin,"_legacy_event_allowed",AsyncMock(return_value=True))
    event,api,raw=_make_group_event(types)
    text="鲸鱼娘，这张内存卡呢" if addressed else "这张内存卡呢"
    event.message_str=text
    event.message_obj.message=[types.Plain(text)]
    raw["message"]=[{"type":"text","data":{"text":text}}]
    event.is_at_or_wake_command=False
    await plugin.route_native_group_message(event)
    assert event.is_at_or_wake_command is addressed
    if addressed:
        quote=Reply(id="quoted",sender_id="456",sender_nickname="另一位同名群友",chain=[types.Plain("我买的是Switch")])
        event.message_obj.message.append(quote)
        request=ProviderRequest(prompt=text,system_prompt="persona",contexts=[
            {"role":"user","content":"之前另一人说了台式机"},
            {"role":"assistant","content":"我之前错猜成了内存条"},
        ])
        # Exercise an actual native Reply in a new event-source capture.
        from app.astrbot_group_context import group_message_record
        event.set_extra("qqchat_group_current_source",group_message_record(event))
        plugin._get_group_router().prepare_request(event,request)
        assert request.prompt.endswith(text)
        assert "speaker_ref" in request.prompt and "我买的是Switch" in request.prompt
        assert "发言者未标注" in request.contexts[0]["content"]
        assert request.contexts[1]["content"].endswith("我之前错猜成了内存条")
        assert "推测不等于真人已确认事实" in request.contexts[1]["content"]
        assert request.extra_user_content_parts
    assert api.calls==[]
