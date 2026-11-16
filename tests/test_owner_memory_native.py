"""Actual AstrBot events, tools and compression, without any network sends."""
import importlib.util
import json
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types, _make_group_event


def load_plugin(root,monkeypatch):
    spec=importlib.util.spec_from_file_location("_owner_native_main",root/"main.py")
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module)
    spec.loader.exec_module(module)
    plugin=module.QQChatPlugin(NS(get_all_stars=list),{"legacy_chat":False,"group_chat_groups":["12345"]})
    from app.config import Settings
    plugin.runtime=NS(_core=NS(settings=Settings(_env_file=None),app=NS(state=NS(db=None))))
    monkeypatch.setattr(plugin,"_legacy_event_allowed",AsyncMock(return_value=True))
    return plugin


async def test_owner_native_group_archive_tools_and_successful_delivery(monkeypatch,tmp_path,owner_enabled):
    root=_find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT",str(tmp_path/"native-astrbot"))
    monkeypatch.chdir(tmp_path)
    types=_load_astrbot_types(root,monkeypatch)
    from app.astrbot_owner_memory import recall
    from app.services import owner_memory
    plugin=load_plugin(root,monkeypatch)
    event,api,raw=_make_group_event(types)
    event.message_obj.group_id="12345"
    async def send_group_msg(**params):
        return await api.call_action("send_group_msg",**params)
    event.bot.send_group_msg=send_group_msg
    await plugin.route_native_group_message(event)
    manager=plugin._get_music_listening()
    assert manager.operations is not None and manager.jobs.operations is manager.operations
    assert manager.jobs.on_delivered is not None
    assert recall(event)["matches"][0]["role"]=="owner"
    # The native send uses a recording API and really sets _has_send_oper.
    event.set_result(event.plain_result("这次是你的内存卡，本鱼分清啦"))
    await plugin.finish_native_group_reply(event)
    assert len(recall(event)["matches"])==1
    await event.send(event.get_result())
    await plugin.finish_native_group_reply(event)
    rows=recall(event)["matches"]
    assert len(rows)==2 and rows[0]["role"]=="assistant"
    assert len(api.calls)==1
    assert "scope" not in json.dumps(rows,ensure_ascii=False)
    # Same name, different QQ does not authorize recall or music profile.
    raw["user_id"]=67890
    event.message_obj.sender.user_id="67890"
    assert json.loads(await plugin.qqchat_recall(event,"内存卡"))["status"]=="denied"
    assert json.loads(await plugin.qqchat_token_usage(event))["status"]=="denied"
    assert json.loads(await plugin.qqchat_forget_owner(event,all_current=True))["status"]=="denied"
    assert owner_memory.ARCHIVE_PATH.exists()


async def test_actual_native_context_retains_over_100_turns_and_preserves_tool_rounds(monkeypatch,tmp_path):
    root=_find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT",str(tmp_path/"native-astrbot"))
    _load_astrbot_types(root,monkeypatch)
    from astrbot.core.agent.context.compressor import LLMSummaryCompressor
    from astrbot.core.agent.context.config import ContextConfig
    from astrbot.core.agent.context.manager import ContextManager
    from astrbot.core.agent.message import Message
    from astrbot.core.provider.entities import LLMResponse
    messages=[Message(role="system",content="大肥鱼人格")]
    for i in range(105):
        messages += [Message(role="user",content=f"[member-a] 第{i}轮约定"),
                     Message(role="assistant",content=f"第{i}轮回答")]
    manager=ContextManager(ContextConfig(max_context_tokens=272000,enforce_max_turns=-1))
    assert len(await manager.process(messages))==211
    # A tool call/result always stays in the same native round.
    messages += [Message(role="user",content="当前完整提问"),
        Message(role="assistant",tool_calls=[{"id":"call-memory","type":"function","function":{
            "name":"qqchat_recall","arguments":"{}"}}]),
        Message(role="tool",tool_call_id="call-memory",content="来自原文的事实"),
        Message(role="assistant",content="基于原文答复")]
    provider=NS(provider_config={"modalities":["text","tool_use"]},text_chat=AsyncMock(
        return_value=LLMResponse(role="assistant",completion_text="[member-a] 主人的明确约定与最新修正，原文可检索")))
    compressor=LLMSummaryCompressor(provider,keep_recent_ratio=.2)
    assert not compressor.should_compress(messages,223040,272000)
    assert compressor.should_compress(messages,223041,272000)
    compacted=await compressor(messages)
    ids=[m.tool_call_id for m in compacted if m.role=="tool"]
    calls=[tc.id for m in compacted for tc in m.tool_calls or []]
    assert ids==calls==["call-memory"]
    assert any(m.content=="当前完整提问" for m in compacted)
    assert len(compacted)<len(messages)


async def test_provider_records_native_response_usage_once_including_stream(monkeypatch,tmp_path):
    root=_find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT",str(tmp_path/"native-astrbot"))
    _load_astrbot_types(root,monkeypatch)
    from app.astrbot_chatgpt_provider import register_chatgpt_provider
    from app.services.usage_ledger import ledger
    provider=register_chatgpt_provider()({"id":"isolated-luna-usage","model":"gpt-6-luna","enable":True,
        "modalities":["text","tool_use"],"chatgpt_credentials_dir":str(tmp_path/"credentials")},{})
    response={"id":"real-sdk-fixture","status":"completed","output":[{"type":"message","content":[
        {"type":"output_text","text":"记住啦"}]}],"usage":{"input_tokens":150,"output_tokens":20,"total_tokens":170,
        "input_tokens_details":{"cached_tokens":50,"cache_write_tokens":10},
        "output_tokens_details":{"reasoning_tokens":5}}}
    monkeypatch.setattr(provider.subscription,"respond",AsyncMock(return_value=response))
    try:
        replies=[r async for r in provider.text_chat_stream(prompt="fixture")]
        assert replies[-1].completion_text=="记住啦"
        report=ledger().report(owner_only=False)
        assert report["calls"]==1
        assert report["known_sums"]["total_tokens"]==170
        assert report["known_sums"]["cache_write_tokens"]==10
    finally:
        await provider.terminate()
