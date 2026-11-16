import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.astrbot_failure_reply import (
    Failure,
    clean_tool_failure,
    describe_failure,
    detect_failure,
    fallback,
    install_send_guard,
    rewrite_message,
    rewrite_raw_text,
)


class Plain:
    def __init__(self,text):self.text=text


def event(question='肥鱼，来份色图',platform='aiocqhttp'):
    extras={}
    return NS(get_platform_name=lambda:platform,get_message_str=lambda:question,
        unified_msg_origin='isolated:GroupMessage:12345',get_extra=lambda k,d=None:extras.get(k,d),
        set_extra=lambda k,v:extras.update({k:v}),send=AsyncMock())


def context(answer='这张图本鱼这次还没拿到，有点不甘心，换个找法再说'):
    provider=NS(get_model=lambda:'gpt-6-luna',text_chat=AsyncMock(return_value=NS(completion_text=answer)))
    return NS(get_using_provider_async=AsyncMock(return_value=provider)),provider


@pytest.mark.parametrize('text,kind',[
    ('❌ 图片接口返回 HTTP 400：invalid_model','unavailable'),
    ('识图接口暂时没响应，稍后再试试','unavailable'),
    ('图片源连接超时，稍后再试试','timeout'),
    ('图片发送回执未确认','uncertain'),
    ('图片已经生成，但没能发送','made_not_delivered'),
    ('语音服务暂时不可用','unavailable'),
    ('这次牌谱复盘处理失败，任务已结束','unavailable'),
    ('聊天模型回复超时了','timeout'),
    ('❌ 设置保存失败，请联系管理员','unavailable'),
    ('设置保存失败','unavailable'),
    ('权限不足，不能执行这个操作','denied'),
    ('未安装依赖，请运行 pip install fixture','unavailable'),
    ('插件执行出错：ModuleNotFoundError: no module named fixture','unavailable'),
    ('语音功能暂时没有配置好，请联系管理员','unavailable'),
    ('生图扩展未启用，请先打开开关','disabled'),
    ('系统维护中，请稍后再试','unavailable'),
    ('{"status":"failed","error":"internal"}','unavailable'),
    ('ChatGPT 授权: HTTP 403 unsupported_country_region_territory request_id=','chat_unavailable'),
    ('All chat models failed: SubscriptionError: ChatGPT 授权: HTTP 403 unsupported_country_region_territory request_id=','chat_unavailable'),
    ('请求失败\n错误类型: SubscriptionError\nChatGPT 授权: HTTP 401 invalid_grant','chat_unavailable'),
    ('图片接口返回 HTTP 403 Forbidden','service_access'),
])
def test_all_feature_notices_classify_real_outcomes(text,kind):
    assert detect_failure(text).kind==kind


@pytest.mark.parametrize('text',[
    '她昨天比赛失败了，本鱼陪她聊了一会儿','API 返回 HTTP 401 表示鉴权失败',
    '这句话“接口返回失败”很像机器人','```json\n{"error":"失败"}\n```',
    '本鱼这次好像没做好，有点难住了','图已经发出去了',
    '😢 今天比赛失败了，我想陪你歇一会儿',
    '☀️ 大肥鱼 · 今日热点\n1. 某模型接口请求失败引发讨论\n来源：公开报道',
    '全局复盘\n第六局的 AI 接口调用失败，本轮完整本地分析如下',
])
def test_normal_chat_quotes_code_and_success_are_not_rewritten(text):
    assert detect_failure(text) is None


async def test_luna_phrases_failure_without_receiving_private_error_and_memoizes_one_event():
    e=event();ctx,p=context()
    text='图片接口返回 HTTP 401：Authorization: Bearer fixture-private-secret'
    message=NS(chain=[Plain(text),NS(type='Image',file='unchanged-image')])
    await rewrite_message(ctx,e,message)
    assert message.chain[0].text=='这张图本鱼这次还没拿到，有点不甘心，换个找法再说'
    assert message.chain[1].file=='unchanged-image'
    args=p.text_chat.await_args.kwargs
    assert 'fixture-private-secret' not in json.dumps(args,ensure_ascii=False)
    assert args['func_tool'] is None and args['contexts']==[]
    await rewrite_message(ctx,e,NS(chain=[Plain(text)]))
    assert p.text_chat.await_count==1


@pytest.mark.parametrize('native_wrapper',[False,True])
async def test_chat_admission_failure_is_local_and_does_not_blame_qq_permission_or_retry_luna(native_wrapper):
    e=event('你会唱歌吗');ctx,p=context()
    text='请求失败\n错误类型: SubscriptionError\n错误信息: ChatGPT 授权: HTTP 403 unsupported_country_region_territory request_id=fixture'
    if native_wrapper:text='All chat models failed: SubscriptionError: ChatGPT 授权: HTTP 403 unsupported_country_region_territory request_id=fixture'
    info=detect_failure(text,question=e.get_message_str())
    assert info==Failure('接话','chat_unavailable')
    message=NS(chain=[Plain(text)],is_llm_result=lambda:native_wrapper)
    await rewrite_message(ctx,e,message)
    assert message.chain[0].text==fallback(info)
    assert not any(word in message.chain[0].text for word in ('权限','不会唱','配置','403','ChatGPT','fixture'))
    await describe_failure(ctx,e,Failure('歌曲'))
    ctx.get_using_provider_async.assert_not_awaited()
    p.text_chat.assert_not_awaited()


async def test_independent_events_use_their_own_expression_without_random_templates():
    ctx,p=context()
    p.text_chat.side_effect=[NS(completion_text='这次没找到合适的图，本鱼再想想'),
                             NS(completion_text='图这次没拿到，稍微有点难住本鱼了')]
    first=await describe_failure(ctx,event(),Failure('图片'))
    second=await describe_failure(ctx,event(),Failure('图片'))
    assert first!=second and p.text_chat.await_count==2


async def test_bad_rephrasing_or_unavailable_chat_model_never_blocks_terminal_delivery():
    e=event();ctx,p=context('已经发给你了，接口只是超时')
    assert await describe_failure(ctx,e,Failure('图片'))==fallback(Failure('图片'))
    e2=event();p.text_chat.side_effect=TimeoutError()
    result=await describe_failure(ctx,e2,Failure('语音'))
    assert '本鱼' in result and '语音' in result
    await describe_failure(ctx,e2,Failure('视频'))
    assert p.text_chat.await_count==2


async def test_event_send_guard_covers_direct_plugin_sends_exactly_once_and_preserves_ack():
    e=event();ctx,_p=context()
    original=e.send
    install_send_guard(ctx,e)
    install_send_guard(ctx,e)
    message=NS(chain=[Plain('图片源连接超时，稍后再试试')])
    await e.send(message)
    original.assert_awaited_once_with(message)
    e.send.assert_awaited_once_with(message)
    assert '接口' not in message.chain[0].text
    original.side_effect=ConnectionError()
    with pytest.raises(ConnectionError):await e.send(NS(chain=[Plain('正常图片说明')]))
    assert original.await_count==2  # no unsafe send retry


async def test_other_platforms_and_technical_explanations_are_unchanged():
    ctx,p=context()
    for e in (event(platform='bilibili'),event('解释一下 HTTP 401 的含义')):
        original=e.send
        install_send_guard(ctx,e)
        text='接口返回 HTTP 401：授权失败'
        assert await rewrite_raw_text(ctx,e,text)==text
        if e.get_platform_name()=='bilibili':assert e.send is original
    p.text_chat.assert_not_awaited()


async def test_normal_model_discussion_of_failures_is_not_a_new_tool_failure():
    e=event('怎么看待这件技术新闻');ctx,p=context()
    message=NS(chain=[Plain('模型训练失败不一定意味着整个项目失败')],is_llm_result=lambda:True)
    await rewrite_message(ctx,e,message)
    assert message.chain[0].text=='模型训练失败不一定意味着整个项目失败'
    p.text_chat.assert_not_awaited()


def result(value,*,error=False):
    return NS(isError=error,content=[NS(type='text',text=json.dumps(value,ensure_ascii=False))])


def test_image_failure_tool_keeps_uncertainty_and_retry_flags_without_diagnostic_text():
    e=event();r=result({'ok':False,'status':'submission_unconfirmed','possible_upstream_execution':True,
        'actual_error':'HTTP 504 fixture-secret','message':'上游接口失败','same_turn_retry_allowed':False,
        'retryable':False,'endpoint':'https://private.invalid','debug':{'api_key':'fixture-secret'}})
    assert clean_tool_failure(e,NS(name='pc_generate_photo'),r)
    data=json.loads(r.content[0].text)
    assert data['status']=='submission_unconfirmed' and data['same_turn_retry_allowed'] is False
    assert data['retryable'] is False and data['possible_upstream_execution'] is True
    assert 'fixture-secret' not in r.content[0].text and 'private.invalid' not in r.content[0].text
    assert e.get_extra('qqchat_last_tool_failure').kind=='uncertain'


def test_file_data_successful_jobs_and_missing_recall_are_not_treated_as_execution_failures():
    e=event()
    for name,value in [('astrbot_file_read_tool',{'status':'failed','message':'file contents'}),
                       ('qqchat_recall',{'status':'not_found'}),('pc_generate_photo',{'status':'started','ok':False})]:
        r=result(value);before=r.content[0].text
        assert not clean_tool_failure(e,NS(name=name),r)
        assert r.content[0].text==before


def test_successful_generation_and_failed_delivery_remain_distinct():
    e=event();r=result({'ok':True,'sent':False,'generation_completed':True,'failure_stage':'delivery',
        'delivery_uncertain':True,'delivery_error':'HTTP timeout'})
    assert clean_tool_failure(e,NS(name='pc_generate_photo'),r)
    value=json.loads(r.content[0].text)
    assert value['ok'] is True and value['generation_completed'] is True and value['sent'] is False
    assert value['public_outcome']['outcome'].startswith('无法确认')


async def test_owned_legacy_headings_change_but_a_real_character_name_does_not():
    e=event();ctx,p=context()
    assert await rewrite_raw_text(ctx,e,'┏ 小丛雨 · 功能菜单 ┓\n内容')=='┏ 大肥鱼 · 功能菜单 ┓\n内容'
    assert await rewrite_raw_text(ctx,e,'✦ 小丛雨图片 ✦')=='✦ 小丛雨图片 ✦'
    p.text_chat.assert_not_awaited()
