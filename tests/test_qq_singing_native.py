import asyncio
import base64
import importlib.util
import json
import sys
import wave
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types, _make_group_event


@pytest.mark.parametrize("delivery_fails", [False, True])
@pytest.mark.parametrize("entry", ["tool", "group_command", "private_command"])
async def test_native_singing_tool_produces_audio_or_one_uncertain_reply_without_retry(monkeypatch, tmp_path, delivery_fails, entry):
    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "native"))
    types = _load_astrbot_types(root, monkeypatch)
    from app import astrbot_singing as singing
    from app.config import Settings
    from app.services.singing_jobs import SingingJobManager
    monkeypatch.setattr("app.services.qq_voice.pack_config", lambda: None)
    spec = importlib.util.spec_from_file_location("_singing_native_plugin", root / "main.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    manager = SingingJobManager(cooldown_seconds=0)
    settings = Settings(voice_enabled=True, singing_enabled=True, voice_profile_default="fish",
        voice_profiles_json=json.dumps({"fish": {"label": "测试鱼", "singing_enabled": True}}))
    plugin = module.QQChatPlugin(NS(), {"legacy_chat": False, "group_chat_groups": ["12345"]})
    plugin.runtime = NS(_core=NS(settings=settings, app=NS(state=NS(singing_jobs=manager,
        db=NS(voice_profile=AsyncMock(return_value="fish"))))))
    monkeypatch.setattr(plugin, "_legacy_event_allowed", AsyncMock(return_value=True))
    e, api, _ = _make_group_event(types)
    e.message_obj.group_id = "12345"
    e.message_str = "鲸鱼娘，给我唱一段《测试曲》"
    e.is_at_or_wake_command = True
    if entry == "private_command":
        e.message_obj.group_id = ""
        e.message_obj.type = types.MessageType.FRIEND_MESSAGE
        e.message_obj.raw_message["message_type"] = "private"
        e.message_obj.raw_message.pop("group_id", None)
    wav = tmp_path / "cover.wav"
    with wave.open(str(wav), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 16000)
    entered, release = asyncio.Event(), asyncio.Event()
    async def generate(query, profile, job_id, cfg, progress, **kwargs):
        assert (query, profile, kwargs["mode"]) == ("测试曲", "fish", "clip")
        entered.set()
        await release.wait()
        await progress("不会发到群里的内部进度")
        return NS(chunks=[NS(path=wav)], song=NS(track=NS(title="测试曲")))
    monkeypatch.setattr(singing, "generate_singing_cover", generate)
    monkeypatch.setattr(singing, "runtime_paths", lambda settings: ())
    monkeypatch.setattr(singing, "archive_singing_job", Mock())
    monkeypatch.setattr(singing, "cleanup_singing_job", Mock())
    recorded = Mock()
    monkeypatch.setattr("app.services.owner_memory.record_assistant", recorded)
    phrase = AsyncMock(return_value="这段歌声本鱼没法确认有没有送到，先不重复发啦")
    monkeypatch.setattr("app.astrbot_failure_reply.describe_failure", phrase)
    async def send_group_msg(**params):
        reply = await api.call_action("send_private_msg" if entry == "private_command" else "send_group_msg", **params)
        if delivery_fails and any(p["type"] == "record" for p in params["message"]):
            raise TimeoutError("test ACK missing")
        return reply
    e.bot.send_group_msg = send_group_msg
    e.bot.send_private_msg = send_group_msg
    if entry == "tool":
        result = json.loads(await plugin.qqchat_sing(e, query="测试曲", mode="clip"))
        assert result["status"] == "accepted"
    else:
        monkeypatch.setattr("app.astrbot_owner_memory.observe", AsyncMock())
        monkeypatch.setattr("app.astrbot_plugin_status.private_companion_photo_hint", lambda *args: None)
        monkeypatch.setattr(plugin, "_get_music_listening", lambda: NS(observe=lambda event: None))
        await plugin.route_native_group_message(e)
        assert e.is_stopped()
    await entered.wait()
    assert not api.calls
    release.set()
    await manager._runner
    payloads = [p for _, call in api.calls for p in call["message"]]
    records = [p for p in payloads if p["type"] == "record"]
    assert len(records) == 1
    assert base64.b64decode(records[0]["data"]["file"].removeprefix("base64://")) == wav.read_bytes()
    assert not any("内部进度" in str(p) for p in payloads)
    job = manager.status(("99999", e.unified_msg_origin, "12345"))
    assert job.status == ("failed" if delivery_fails else "succeeded")
    assert recorded.call_count == (0 if delivery_fails else 1)
    if delivery_fails:
        assert phrase.call_args.args[2].kind == "uncertain"
        assert sum("没法确认" in p.get("data", {}).get("text", "") for p in payloads) == 1
    else:
        assert not phrase.called
    await manager.aclose()


@pytest.mark.parametrize('target',['self','other','private'])
async def test_native_capability_request_does_not_need_chat_authorization(monkeypatch,tmp_path,target):
    root=_find_project_root()
    monkeypatch.setenv('ASTRBOT_ROOT',str(tmp_path/'native'))
    types=_load_astrbot_types(root,monkeypatch)
    from app import astrbot_singing as singing
    from app.config import Settings
    from app.services.singing_jobs import SingingJobManager
    monkeypatch.setattr('app.services.qq_voice.pack_config',lambda:None)
    spec=importlib.util.spec_from_file_location('_singing_question_native_plugin',root/'main.py')
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module)
    spec.loader.exec_module(module)
    manager=SingingJobManager(cooldown_seconds=0)
    provider=AsyncMock(side_effect=AssertionError('Luna must not be called for this local question'))
    plugin=module.QQChatPlugin(NS(get_using_provider_async=provider),{'legacy_chat':False,'group_chat_groups':['12345']})
    plugin.runtime=NS(_core=NS(settings=Settings(voice_enabled=True,singing_enabled=True,voice_profile_default='fish',
        voice_profiles_json=json.dumps({'fish':{'label':'测试鱼','singing_enabled':True}})),
        app=NS(state=NS(singing_jobs=manager,db=NS(voice_profile=AsyncMock(return_value='fish'))))))
    monkeypatch.setattr(plugin,'_legacy_event_allowed',AsyncMock(return_value=True))
    monkeypatch.setattr('app.astrbot_owner_memory.observe',AsyncMock())
    monkeypatch.setattr('app.astrbot_plugin_status.private_companion_photo_hint',lambda *args:None)
    monkeypatch.setattr(plugin,'_get_music_listening',lambda:NS(observe=lambda event:None))
    monkeypatch.setattr(singing,'runtime_paths',lambda settings:())
    e,api,_=_make_group_event(types)
    e.message_obj.group_id='12345'
    e.message_str='你会唱歌吗'
    e.is_at_or_wake_command=True
    e.message_obj.message=[]
    e.message_obj.raw_message['message']=[{'type':'at','data':{'qq':'99999' if target=='self' else 'another-member'}}]
    if target=='private':
        e.message_obj.group_id=''
        e.message_obj.type=types.MessageType.FRIEND_MESSAGE
        e.message_obj.raw_message['message_type']='private'
        e.message_obj.raw_message.pop('group_id',None)
    async def send(**params):return await api.call_action('send_private_msg' if target=='private' else 'send_group_msg',**params)
    e.bot.send_group_msg=send
    e.bot.send_private_msg=send
    await plugin.route_native_group_message(e)
    assert len(api.calls)==(0 if target=='other' else 1)
    assert not manager._active
    provider.assert_not_awaited()
    if target!='other':
        assert e.is_stopped()
        assert any('想听本鱼唱哪首' in p.get('data',{}).get('text','') for p in api.calls[0][1]['message'])
    await manager.aclose()


async def test_native_provider_request_receives_real_tool_capability(monkeypatch, tmp_path):
    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "native"))
    _load_astrbot_types(root, monkeypatch)
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    from astrbot.core.provider.entities import ProviderRequest
    from test_qq_singing import event

    from app.astrbot_singing import prepare_audio_capabilities
    from app.config import Settings
    monkeypatch.setattr("app.services.qq_voice.pack_config", lambda: None)
    monkeypatch.setattr("app.astrbot_singing.runtime_paths", lambda settings: ())
    core = NS(settings=Settings(voice_enabled=True, singing_enabled=True, voice_profiles_json="{}", voice_profile_default="default"),
              app=NS(state=NS(singing_jobs=object(), db=NS(voice_profile=AsyncMock(return_value="default")))))
    request = ProviderRequest(prompt="你会唱歌吗", func_tool=ToolSet(tools=[
        FunctionTool(name=name, description="existing tool", parameters={}) for name in ("qqchat_sing", "qqchat_send_voice")]))
    await prepare_audio_capabilities(core, event(), request)
    assert '"语音可调用": true' in request.system_prompt
    assert '"唱歌可调用": true' in request.system_prompt
    original = request.func_tool
    bili = ProviderRequest(prompt="普通聊天", system_prompt="B站原文", func_tool=original)
    await prepare_audio_capabilities(core, event(platform="bilibili"), bili)
    assert bili.system_prompt == "B站原文" and bili.func_tool.get_tool("qqchat_sing") is None
    assert original.get_tool("qqchat_sing") is not None
    assert bili.func_tool.get_tool("qqchat_send_voice") is original.get_tool("qqchat_send_voice")
