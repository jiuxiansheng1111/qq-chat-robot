"""Use the installed native AstrBot adapter, not a mirror of its implementation."""

import copy

import pytest


@pytest.mark.parametrize("streaming", [False, True])
async def test_native_astrbot_context_and_tool_round_trip(monkeypatch, tmp_path, streaming):
    from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types

    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "native-astrbot"))
    monkeypatch.chdir(tmp_path)
    _load_astrbot_types(root, monkeypatch)
    from astrbot.core.agent.message import (
        AssistantMessageSegment,
        Message,
        TextPart,
        ToolCallMessageSegment,
    )
    from astrbot.core.agent.tool import FunctionTool, ToolSet
    from astrbot.core.provider.entities import ToolCallsResult
    from astrbot.core.provider.manager import ProviderManager
    from astrbot.core.star.context import Context

    from app.astrbot_chatgpt_provider import PROVIDER_TYPE, register_chatgpt_provider

    adapter = register_chatgpt_provider()
    assert register_chatgpt_provider() is adapter
    manager = object.__new__(ProviderManager)
    manager.provider_sources_config = []
    manager.provider_settings = {}
    manager.provider_insts = []
    manager.inst_map = {}
    manager.default_chat_provider_id = "isolated-luna"
    manager.curr_provider_inst = None
    await manager.load_provider({
        "id": "isolated-luna", "type": PROVIDER_TYPE, "provider_type": "chat_completion",
        "enable": True, "api_base": "https://example.invalid", "key": ["not-an-api-key"],
        "model": "gpt-6-luna", "modalities": ["text", "tool_use"],
        "chatgpt_credentials_dir": str(tmp_path / "credentials"),
        "custom_extra_body": {"max_tokens": 4096, "temperature": 0.7, "reasoning": {"effort": "low"}},
    })
    provider = await manager.get_provider_by_id("isolated-luna")
    assert isinstance(provider, adapter)
    sent = []
    response = {"id": "resp_1", "status": "completed", "output": [
        {"type": "function_call", "namespace": "qqchat", "name": "qqchat.draw",
         "call_id": "call_1", "arguments": '{"prompt":"猫咪"}'},
    ]}

    async def respond(payload):
        sent.append(copy.deepcopy(payload))
        return copy.deepcopy(response)

    monkeypatch.setattr(provider.subscription, "respond", respond)
    tools = ToolSet()
    tools.add_tool(FunctionTool(
        name="draw", description="调用原有图片服务",
        parameters={"type": "object", "properties": {"prompt": {"type": "string"}}},
    ))
    context = object.__new__(Context)
    context.provider_manager = manager
    try:
        kwargs = {
            "chat_provider_id": "isolated-luna", "prompt": "画张猫咪", "system_prompt": "保持原角色",
            "contexts": [Message(role="user", content=[TextPart(text="历史对话")])],
            "tools": tools, "request_max_retries": 1,
        }
        if streaming:
            stream_args = {key: value for key, value in kwargs.items()
                           if key not in {"chat_provider_id", "tools"}}
            replies = [item async for item in provider.text_chat_stream(**stream_args, func_tool=tools)]
            reply = replies[-1]
        else:
            reply = await context.llm_generate(**kwargs)
        assert reply.tools_call_name == ["draw"]
        assert reply.tools_call_ids == ["call_1"]
        assert reply.tools_call_args == [{"prompt": "猫咪"}]
        assert sent[0]["instructions"] == "保持原角色"
        assert sent[0]["input"][0]["content"] == [{"type": "input_text", "text": "历史对话"}]
        assert sent[0]["tools"][0]["name"] == "qqchat"
        assert "temperature" not in sent[0] and "max_output_tokens" not in sent[0]
        history = ToolCallsResult(
            tool_calls_info=AssistantMessageSegment(tool_calls=[{
                "id": "call_1", "type": "function", "function": {
                "name": "draw", "arguments": '{"prompt":"猫咪"}',
            }}]), tool_calls_result=[ToolCallMessageSegment(
                tool_call_id="call_1", content="已通过原 API 生成猫咪",
            )],
        )
        response["output"] = [{"type": "message", "content": [{
            "type": "output_text", "text": "猫咪图片已经发出啦。",
        }]}]
        final = await context.llm_generate(
            chat_provider_id="isolated-luna", prompt=None, contexts=kwargs["contexts"],
            tool_calls_result=history, tools=tools, request_max_retries=1,
        )
        assert final.completion_text == "猫咪图片已经发出啦。"
        assert any(item.get("call_id") == "call_1" and item.get("namespace") == "qqchat"
                   for item in sent[-1]["input"])
        assert any(item.get("type") == "function_call_output" and
                   item.get("output") == "已通过原 API 生成猫咪" for item in sent[-1]["input"])
    finally:
        await provider.terminate()


@pytest.mark.parametrize("rejected", [False, True])
async def test_native_search_preflight_uses_oauth_once_and_preserves_rejections(monkeypatch, tmp_path, rejected):
    import json
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock

    from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types
    root = _find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "native-search"))
    monkeypatch.chdir(tmp_path)
    _load_astrbot_types(root, monkeypatch)
    from test_astrbot_search import GroupEvent

    from app.astrbot_chatgpt_provider import register_chatgpt_provider
    from app.astrbot_search import _plan_group_search
    from app.chatgpt_subscription import SubscriptionError
    provider = register_chatgpt_provider()({
        "id": "isolated-luna", "model": "gpt-6-luna", "type": "qqchat_chatgpt_subscription",
        "key": ["not-a-real-key"], "api_base": "https://example.invalid", "chatgpt_credentials_dir": str(tmp_path / "credentials"),
    }, {})
    respond = AsyncMock(return_value={"id": "synthetic", "status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps({
            "search": True, "query": "NiKo donk 比较", "recency": "oneMonth", "purpose": "game_opinion"})}]}]})
    if rejected:
        respond.side_effect = SubscriptionError("HTTP 403 synthetic-rejection")
    monkeypatch.setattr(provider.subscription, "respond", respond)
    ctx = NS(get_current_chat_provider_id=AsyncMock(return_value="isolated-luna"), get_provider_by_id=lambda _: provider)
    try:
        if rejected:
            with pytest.raises(SubscriptionError):
                await _plan_group_search(ctx, GroupEvent(), NS(contexts=[]))
        else:
            assert (await _plan_group_search(ctx, GroupEvent(), NS(contexts=[])))["purpose"] == "game_opinion"
        respond.assert_awaited_once()
        body = respond.await_args.args[0]
        assert body["store"] is False and body["stream"] is True and body["model"] == "gpt-6-luna"
    finally:
        await provider.terminate()
