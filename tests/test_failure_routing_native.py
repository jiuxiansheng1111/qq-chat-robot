import importlib.util
import json
import sys
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types, _make_group_event


async def test_actual_tool_failure_and_native_send_use_one_natural_result(monkeypatch,tmp_path):
    root=_find_project_root()
    monkeypatch.setenv('ASTRBOT_ROOT',str(tmp_path/'native'))
    monkeypatch.chdir(tmp_path)
    types=_load_astrbot_types(root,monkeypatch)
    from astrbot.core.agent.tool import FunctionTool
    from astrbot.core.message.message_event_result import ResultContentType
    from mcp.types import CallToolResult, TextContent
    spec=importlib.util.spec_from_file_location('_failure_native_plugin',root/'main.py')
    mod=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,spec.name,mod);spec.loader.exec_module(mod)
    provider=NS(get_model=lambda:'gpt-6-luna',text_chat=AsyncMock(return_value=NS(completion_text='这张图本鱼这次没拿到，稍微有点难住了')))
    context=NS(get_using_provider_async=AsyncMock(return_value=provider),get_all_stars=list)
    plugin=mod.QQChatPlugin(context,{'legacy_chat':False,'group_chat_groups':['12345']})
    plugin.runtime=NS()
    monkeypatch.setattr(plugin,'_legacy_event_allowed',AsyncMock(return_value=True))
    event,api,_raw=_make_group_event(types)
    event.message_obj.group_id='12345'
    assert mod._QQChatEarlyFilter().filter(event,{})
    result=CallToolResult(content=[TextContent(type='text',text=json.dumps({
        'status':'failed','ok':False,'actual_error':'HTTP 400 fixture-private-token','same_turn_retry_allowed':False}))])
    await plugin.keep_failure_diagnostics_private(event,FunctionTool(name='pc_generate_photo',description='生成图片',parameters={}),None,result)
    assert 'fixture-private-token' not in result.content[0].text
    event.set_result(event.plain_result('这次接口返回 HTTP 400，图片生成失败'))
    event.get_result().set_result_content_type(ResultContentType.LLM_RESULT)
    await plugin.phrase_qq_function_failure(event)
    async def send_group_msg(**params):return await api.call_action('send_group_msg',**params)
    event.bot.send_group_msg=send_group_msg
    await event.send(event.get_result())
    assert len(api.calls)==1
    text=next(x['data']['text'] for x in api.calls[0][1]['message'] if x['type']=='text')
    assert text=='这张图本鱼这次没拿到，稍微有点难住了'
    assert provider.text_chat.await_count==1


@pytest.mark.parametrize('model_result',[False,True])
async def test_native_chat_region_error_is_sent_once_without_another_provider_call(monkeypatch,tmp_path,model_result):
    root=_find_project_root()
    monkeypatch.setenv('ASTRBOT_ROOT',str(tmp_path/'native'))
    monkeypatch.chdir(tmp_path)
    types=_load_astrbot_types(root,monkeypatch)
    from astrbot.core.message.message_event_result import ResultContentType

    from app.astrbot_failure_reply import install_send_guard
    provider=AsyncMock(side_effect=AssertionError('must not retry rejected chat authorization'))
    event,api,_=_make_group_event(types)
    event.message_obj.group_id='12345'
    install_send_guard(NS(get_using_provider_async=provider),event)
    async def send(**params):return await api.call_action('send_group_msg',**params)
    event.bot.send_group_msg=send
    message=event.plain_result('All chat models failed: SubscriptionError: ChatGPT 授权: HTTP 403 unsupported_country_region_territory request_id=')
    if model_result:message.set_result_content_type(ResultContentType.LLM_RESULT)
    await event.send(message)
    assert len(api.calls)==1
    texts=[p['data']['text'] for p in api.calls[0][1]['message'] if p['type']=='text']
    assert texts==['本鱼这会儿没能接上话，刚才那句没处理好']
    provider.assert_not_awaited()
