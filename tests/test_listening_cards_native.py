"""Actual AstrBot JSON cards must wake the QQ music path without sending test messages."""

from __future__ import annotations

import importlib.util
import json
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types, _make_group_event


@pytest.mark.parametrize("private,allowed",[(True,True),(False,True),(True,False)])
async def test_native_json_music_card_passes_early_filter_and_acl_before_becoming_llm_input(monkeypatch,tmp_path,private,allowed):
    root=_find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT",str(tmp_path/"isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    types=_load_astrbot_types(root,monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    from astrbot.api.message_components import Json

    from app.astrbot_listening import MusicListening
    from app.astrbot_soundtry import SoundtryJobs
    from app.services.listening_memory import ListeningMemory

    spec=importlib.util.spec_from_file_location("_music_cards_native_main",root/"main.py")
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module)
    spec.loader.exec_module(module)
    plugin=module.QQChatPlugin(NS(get_registered_star=lambda name:None),{
        "legacy_chat":False,"group_chat_groups":["12345"],"natural_commands_enabled":True})
    plugin.runtime=object()
    manager=MusicListening(SoundtryJobs(),ListeningMemory(tmp_path/"music.db"))
    monkeypatch.setattr(plugin,"_get_music_listening",lambda:manager)
    monkeypatch.setattr(plugin,"_legacy_event_allowed",AsyncMock(return_value=allowed))
    e,api,raw=_make_group_event(types)
    if private:
        e.message_obj.type=types.MessageType.FRIEND_MESSAGE
        e.message_obj.group_id=""
        raw['message_type']='private'
        raw.pop('group_id',None)
    link="https://st.music.163.com/listen-together/share/index.html?roomId=test-room&inviterId=456&songId=123"
    card=json.dumps({"app":"com.tencent.tuwen.lua","meta":{"news":{"jumpUrl":link}}})
    e.message_obj.message=[Json(data=card)]
    raw['message']=[{'type':'json','data':{'data':card}}]
    e.message_str=""
    e.is_at_or_wake_command=False
    assert module._QQChatEarlyFilter().filter(e,{})
    await plugin.route_native_group_message(e)
    if allowed:
        # Private cards are direct invitations. A bare share in a busy group
        # is observed for later addressed follow-up, without unsolicited chat.
        assert e.is_at_or_wake_command is private and link in e.get_message_str()
        assert any(isinstance(part,types.Plain) and link in part.text for part in e.message_obj.message)
        assert manager.invite_for(e)['room_id']=='test-room'
        assert not e.is_stopped()
    else:
        assert e.is_stopped() and not manager.recent_invites
        assert e.get_message_str()==""
    assert api.calls==[]
