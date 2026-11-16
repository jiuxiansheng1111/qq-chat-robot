"""QQ failures keep their real outcome while the current persona explains it.

No raw exception, endpoint or credential is sent to the phrasing call. This is
an event-local send adapter, not a patch to AstrBot or another platform.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from types import SimpleNamespace

from app.astrbot_qq_persona import is_qq
from app.services.chat_punctuation import natural_chat_text

_LOGGER=logging.getLogger(__name__)
_STATE="qqchat_failure_presentation"
_BOUND="qqchat_failure_send_bound"
_LITERALS=re.compile(r'```[\s\S]*?```|`[^`\n]*`|“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"')
_TECH=re.compile(r"接口|API\b|HTTP\s*[45]\d\d|Key\b|token\b|Traceback|Exception|SubscriptionError|unsupported_country_region_territory|"
                 r"(?:ModuleNotFound|Import|Connection|Timeout|Runtime|Value|Type|HTTP|API)Error|pip\s+install|缺少依赖|未安装|"
                 r"服务器|服务端|后端|上游|回执|调用|未配置|没有配置好|未启用|未开启|尚未开启|维护中|配置.*(?:错误|无效)|"
                 r"(?:服务|模型|图片源|图源|识图).{0,8}(?:失败|繁忙|不可用|没响应|无响应|超时)",re.IGNORECASE)
_FAILED=re.compile(r"失败|异常|出错|错误|超时|繁忙|暂不可用|暂时不可用|不可用|未完成|没完成|"
                   r"未响应|没响应|无响应|未取回|没取回|未确认|没确认|未连接|未发送|没能发送|未能发送|未送达|"
                   r"权限不足|没有权限|无权限|尚未开启|未开启|未启用|未配置|没有配置好|维护中|本群未开启|未安装|缺少依赖|unsupported_country_region_territory|(?:Error|Exception)\b|\berror\b|\bfailed\b|\btimeout\b|HTTP\s*[45]\d\d",re.IGNORECASE)
_BOILERPLATE=re.compile(r"^[❌⚠️🚫😢⛔\s]*(?:error\s*[:：]|错误[:：]|出了点问题[:：]|(?:LLM\s*)?请求失败|"
    r"(?:权限不足|没有权限|无权限|本群未开启)|(?:设置|保存|切换|开启|关闭|修改|读取|下载|上传|获取|发送|查询|识别|转写|合成|播放|加入).{0,18}(?:失败|出错|异常|不可用))",re.IGNORECASE)
_FEATURES=(("图片",r"图片|图源|图片源|识图|生图|插画|色图|涩图|发图|画图|画一|菜单"),
           ("歌曲",r"歌|音乐|音源|音轨|陪听|一起听"),("语音",r"语音|声音|音色|朗读"),
           ("视频",r"视频|画面|字幕"),("牌谱",r"牌谱|雀魂|复盘"),("查询",r"搜索|查询|联网|天气|翻译"))
_OUTCOMES={
    "unavailable":"这次未能完成，没有成功结果，不知道的原因不猜",
    "empty":"这次没有找到合适的内容，没有发送图片或结果",
    "timeout":"这次等待结束仍未取得结果，当前尝试已停止，不承诺继续在后台重试",
    "busy":"这次暂时无法继续处理，没有成功结果，不承诺已经排队或自动重试",
    "denied":"当前操作无法执行，保留必要的权限或访问限制事实，不展开后台配置",
    "service_access":"本次服务访问受限，未能完成；不能据此断言聊天对象没有群权限，也不能说语音未配置",
    "chat_unavailable":"这次接话未能完成，原因在当前聊天服务的访问状态；不要求聊天对象申请群权限，不推断其他功能不可用",
    "disabled":"这项功能当前没有开启，本次未执行，自然说明眼前状态，不把它说成已经执行成功或永久不会",
    "delivery":"本次内容没有确认送达，不说已经发出，不自动重发",
    "made_not_delivered":"内容已取得或生成，但这次没有确认送达，不说用户已经收到，不重复提交任务",
    "uncertain":"无法确认这次是否已经送达或完成，请对方先看看，不断言成功或失败，不重复提交或发送",
}


@dataclass(frozen=True)
class Failure:
    feature:str="这件事"
    kind:str="unavailable"

    @property
    def facts(self):
        return {"feature":self.feature,"outcome":_OUTCOMES[self.kind]}


def _extra(event,key,default=None):
    getter=getattr(event,"get_extra",None)
    return getter(key,default) if callable(getter) else getattr(event,"_qqchat_extras",{}).get(key,default)


def _put(event,key,value):
    setter=getattr(event,"set_extra",None)
    if callable(setter):setter(key,value)
    else:
        if not hasattr(event,"_qqchat_extras"):event._qqchat_extras={}
        event._qqchat_extras[key]=value


def feature_name(text):
    if re.search(r"^[❌⚠️\s]*(?:设置|保存|切换|开启|关闭|修改).{0,16}(?:失败|出错|不可用)",str(text)):
        return "设置"
    return next((name for name,pattern in _FEATURES if re.search(pattern,str(text))),"这件事")


def detect_failure(text:str,*,known=False,question="") -> Failure|None:
    if not isinstance(text,str) or not text.strip():return None
    if text.lstrip().startswith("{"):
        try:
            structured=_tool_failure(json.loads(text),"")
            if structured:return structured
        except (ValueError,TypeError):pass
    plain=_LITERALS.sub("",text).strip()
    head=plain.splitlines()[0] if plain else ""
    # Quoted error examples and programming explanations are not an execution
    # failure. A real tool failure is supplied separately by its native hook.
    if not known and re.search(r"原理|含义|表示|代表|示例|例如|代码如下|错误码的意思",plain):return None
    if not known and not (_FAILED.search(head) and (_TECH.search(head) or _BOILERPLATE.search(head) or re.search(
            r"^(?:[❌⚠️😢🚫\s]*)(?:这次|本次|暂时|目前|图片|视频|歌曲|声音|牌谱|菜单|翻译|联网|搜索).{0,32}(?:失败|超时|不可用|未发送|没能发送|未能发送|未送达)",head))):
        return None
    if not known and not _TECH.search(plain) and re.search(r"本鱼|我这次|我好像|有点难住|有点为难",plain):return None
    kind="unavailable"
    if re.search(r"未确认|没确认|无法确认|未能确认|回执|可能.*(?:已|仍在)",plain):kind="uncertain"
    elif re.search(r"已(?:经)?(?:生成|完成|取得|拿到).*(?:未|没|失败)",plain):kind="made_not_delivered"
    elif re.search(r"发送失败|上传失败|发图失败",plain):kind="delivery"
    elif re.search(r"超时|timeout",plain,re.IGNORECASE):kind="timeout"
    elif re.search(r"繁忙|频繁|限流|429|排队",plain):kind="busy"
    elif re.search(r"HTTP\s*(?:401|403)\b|unsupported_country_region_territory",plain,re.IGNORECASE):
        kind="chat_unavailable" if re.search(r"ChatGPT|SubscriptionError|qqchat-chatgpt-luna",plain,re.IGNORECASE) else "service_access"
    elif re.search(r"权限|拒绝",plain):kind="denied"
    elif re.search(r"尚未开启|未开启|未启用",plain):kind="disabled"
    elif re.search(r"没有.*结果|未找到|没找到|没有可用|均下载失败",plain):kind="empty"
    if kind=="chat_unavailable":return Failure("接话",kind)
    feature=feature_name(plain)
    return Failure(feature_name(question) if feature=="这件事" else feature,kind)


def fallback(info:Failure) -> str:
    name=info.feature
    if info.kind=="uncertain":return "这次我还没法确认有没有送到你那边，你先看一眼，本鱼先不重复发"
    if info.kind=="made_not_delivered":return f"{name}本鱼拿到了，可这次没能确认送到你那边，我先不重复发"
    if info.kind=="delivery":return f"这次没能把{name}送过去，有点难住本鱼了"
    if info.kind=="empty":return f"这次没找到合适的{name}，可能得换个找法了"
    if info.kind=="busy":return "本鱼这会儿有点忙不过来，等一小会儿再叫我吧"
    if info.kind=="timeout":return f"{name}这次等了好一会儿还是没拿到结果，本鱼先停下了"
    if info.kind=="denied":return "这件事本鱼现在还做不到，得有相应的权限才行"
    if info.kind=="service_access":return f"{name}这次没能办成，卡在本鱼这边了"
    if info.kind=="chat_unavailable":return "本鱼这会儿没能接上话，刚才那句没处理好"
    if info.kind=="disabled":return f"{name}这项本鱼还没开起来，这次先办不了"
    return f"{name}这次好像还做不到，有点难住本鱼了"


def _valid_reply(text,info):
    if not text or len(text)>500 or _TECH.search(_LITERALS.sub("",text)):return False
    for match in re.finditer(r"已经(?:发出|发送|加入|完成)|已发送|发给你了|已经收到了",text):
        if not re.search(r"没|未|不|无法|没法",text[max(0,match.start()-12):match.start()]):return False
    return not (info.kind == "uncertain" and re.search(r"(?:肯定|确定|绝对).{0,5}(?:失败|没发|成功)", text))


async def describe_failure(context,event,info:Failure) -> str:
    if not is_qq(event):return fallback(info)
    state=_extra(event,_STATE)
    if not isinstance(state,dict):state={"cache":{},"cooldown":0};_put(event,_STATE,state)
    key=info.feature+":"+info.kind
    if key in state["cache"]:return state["cache"][key]
    answer=fallback(info)
    if info.kind=="chat_unavailable":
        # Rephrasing through the same rejected provider cannot repair admission.
        state["cooldown"]=time.monotonic()+60
    if info.kind!="chat_unavailable" and context is not None and time.monotonic()>=state["cooldown"]:
        try:
            provider=await context.get_using_provider_async(event.unified_msg_origin)
            # This feature phrases only; it never chooses another chat model.
            if provider is not None and str(provider.get_model()).casefold()=="gpt-6-luna":
                from app.astrbot_identity import QQ_PERSONA_PATH
                from app.services.usage_ledger import usage_purpose
                system=QQ_PERSONA_PATH.read_text(encoding="utf-8").strip()+(
                    "\n本轮只替本鱼把一次功能未完成的事实自然说出来，不执行功能，不调用工具。"
                    "像正在跟对方聊天，允许有点为难或不好意思，怎么表达由本鱼结合事情判断。"
                    "不照念例句，不固定道歉或结尾，不机械说都是对方要求太难。"
                    "只说提供的事实，不提接口、错误码、Key、服务器、后台配置或内部排障。"
                    "原因不清楚就不编超时、排队、欠费或权限；不把未完成或送达不确定说成成功，也不承诺自动重试。"
                    "直接给对方简短自然的回复，不输出解释这段话的说明。")
                # Only trusted coarse facts enter this request, never the raw
                # error or arbitrary text supplied by a failed service.
                with usage_purpose("failure_reply"):
                    response=await asyncio.wait_for(provider.text_chat(prompt=json.dumps(info.facts,ensure_ascii=False),
                        system_prompt=system,contexts=[],func_tool=None,request_max_retries=1),12)
                candidate=natural_chat_text(str(getattr(response,"completion_text","") or "").strip())
                if _valid_reply(candidate,info):answer=candidate
        except asyncio.CancelledError:raise
        except Exception as exc:  # noqa: BLE001 -- External providers can raise adapter-specific errors during fallback.
            state["cooldown"]=time.monotonic()+60
            _LOGGER.debug("QQ自然失败回复本轮使用本地表达 error_type=%s",type(exc).__name__)
    if len(state["cache"])>=8:state["cache"].pop(next(iter(state["cache"])))
    state["cache"][key]=answer
    return answer


async def rewrite_message(context,event,message):
    if not is_qq(event):return message
    known=_extra(event,"qqchat_last_tool_failure")
    question=str(getattr(event,"get_message_str",lambda:"")())
    explaining=bool(not known and re.search(r"解释|什么意思|含义|原理|示例|举例|原样|错误原文|排障日志",question)
                    and re.search(r"HTTP|接口|API|报错|错误|代码",question,re.IGNORECASE))
    active_result=getattr(event,"get_result",lambda:None)()
    model_result=any(bool(getattr(value,"is_llm_result",lambda:False)()) for value in (message,active_result) if value is not None)
    for part in getattr(message,"chain",()) or ():
        if type(part).__name__!="Plain":continue
        original=str(getattr(part,"text","") or "")
        # Only known bot-owned headings/self status, never character facts,
        # quotes, voice names or the body of another member's message.
        for old,new in (("┏ 小丛雨 · 功能菜单 ┓","┏ 大肥鱼 · 功能菜单 ┓"),
                        ("小丛雨好感度：","好感度：")):
            if original.startswith(old):original=new+original[len(old):]
        if original.startswith("吾辈现在是“小丛雨”，没有在夺舍"):
            original="本鱼是鲸鱼娘，现在没在扮演别人"
        part.text=original
        if explaining:continue
        if model_result and not known and not re.match(r"^[❌⚠️\s]*(?:接口(?:返回|调用)|HTTP\s*[45]\d\d|API\s*(?:error|返回)|error\s*[:：]|出了点问题[:：]|All chat models failed:|LLM\s*请求失败[:：])",original,re.IGNORECASE):
            continue
        # A natural answer about failure is already correct. Only boilerplate
        # and technical notices need an additional phrasing call.
        info=detect_failure(original,question=question)
        if info is not None:
            if isinstance(known,Failure) and info.feature=="这件事":info=known
            part.text=await describe_failure(context,event,info)
    return message


def install_send_guard(context,event):
    if not is_qq(event) or _extra(event,_BOUND,False):return
    original=getattr(event,"send",None)
    if not callable(original):return
    _put(event,_BOUND,True)
    event.send=_SendGuard(context,event,original)


class _SendGuard:
    def __init__(self,context,event,original):
        self.context,self.event,self.__wrapped__=context,event,original

    def __getattr__(self,name):
        return getattr(self.__wrapped__,name)

    async def __call__(self,message,*args,**kwargs):
        try:
            await rewrite_message(self.context,self.event,message)
        except asyncio.CancelledError:raise
        except Exception as exc:  # noqa: BLE001 -- Keep the original outgoing message if rendering fails.
            _LOGGER.warning("QQ失败表达适配本轮未完成 error_type=%s",type(exc).__name__)
            for part in getattr(message,"chain",()) or ():
                if type(part).__name__=="Plain":
                    info=detect_failure(str(getattr(part,"text","") or ""))
                    if info:part.text=fallback(info)
        # Return the real ACK immediately. Background producers must mark a
        # confirmed delivery before running their own memory callback.
        return await self.__wrapped__(message,*args,**kwargs)


def _tool_failure(value,name):
    if not isinstance(value,dict):return None
    status=str(value.get("status","")).casefold()
    stage=str(value.get("failure_stage",""))
    if status in {"not_found","missing_invitation","missing_attachment","started","accepted","ok","sent","joined","saved"}:return None
    failed=value.get("ok") is False or value.get("success") is False or status in {
        "failed","error","unavailable","disabled","denied","not_joined","same_account","join_unconfirmed",
        "submission_unconfirmed","result_retrieval_failed","busy","rate_limited","unconfirmed"}
    made=bool(value.get("generation_completed") or value.get("upstream_generated"))
    if not failed and not ((made or stage=="delivery") and value.get("sent") is False and not value.get("delivery_deferred")):return None
    kind="unavailable"
    if value.get("delivery_uncertain") or value.get("possible_upstream_execution") or status in {"unconfirmed","submission_unconfirmed","join_unconfirmed"}:kind="uncertain"
    elif made:kind="made_not_delivered"
    elif stage=="delivery":kind="delivery"
    elif status in {"busy","rate_limited"}:kind="busy"
    elif status=="denied":kind="denied"
    elif status=="disabled":kind="disabled"
    feature=feature_name(name+" "+str(value.get("message","")))
    if name.startswith("pc_") and "photo" in name:feature="图片"
    return Failure(feature,kind)


def clean_tool_failure(event,tool,tool_result):
    """Keep status/retry flags for execution; replace diagnostic prose for LLM."""
    if not is_qq(event) or tool_result is None:return False
    name=str(getattr(tool,"name",""))
    # File/search contents can themselves be JSON describing somebody else's
    # failure. Do not treat their quoted payload as this tool's own outcome.
    if not (name.startswith(("qqchat_","pc_")) or getattr(tool_result,"isError",False)):return False
    changed=False
    for part in getattr(tool_result,"content",()) or ():
        if getattr(part,"type","")!="text":continue
        text=str(getattr(part,"text","") or "")
        try:value=json.loads(text)
        except (ValueError,TypeError):
            if not getattr(tool_result,"isError",False) and detect_failure(text) is None:continue
            value={"status":"failed","message":text}
        info=_tool_failure(value,name)
        if info is None:continue
        _put(event,"qqchat_last_tool_failure",info)
        value=dict(value)
        safe="本次"+info.facts["outcome"]
        for key in ("message","note","error","actual_error","failure_reason","delivery_error","traceback"):
            if key in value:value[key]=safe
        for key in tuple(value):
            if re.search(r"api[_-]?key|token|cookie|authorization|headers|debug|diagnostic|endpoint|base_url|request_body|response_body",key,re.IGNORECASE):
                value.pop(key)
        value["actionable_hint"]="按真实结果及原有重试标记处理，用大肥鱼自己的口吻自然说明，不复述内部诊断，不编原因"
        value["final_response_instruction"]="任务确实没能完成时自然交代，如何表达随语境发挥，不套固定句子；送达不确定时先让对方看看，不断言成功或失败"
        value["public_outcome"]=info.facts
        part.text=json.dumps(value,ensure_ascii=False)
        _LOGGER.info("QQ功能失败已分离内部诊断 tool=%s outcome=%s",re.sub(r"[^A-Za-z0-9_-]","",name)[:80],info.kind)
        changed=True
    return changed


async def rewrite_raw_text(context,event,text):
    """The legacy OneBot callback sends strings rather than MessageChains."""
    part=type("Plain",(),{})();part.text=text
    await rewrite_message(context,event,SimpleNamespace(chain=[part]))
    return part.text
