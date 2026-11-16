"""Small Luna/recall probe with fictional isolated records; never sends to QQ."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def main():
    from astrbot.core.agent.message import AssistantMessageSegment,ToolCallMessageSegment
    from astrbot.core.agent.tool import FunctionTool,ToolSet
    from astrbot.core.provider.entities import ProviderRequest,ToolCallsResult
    from app.astrbot_chatgpt_provider import register_chatgpt_provider
    from app.astrbot_owner_memory import prepare,recall
    from app.astrbot_qq_persona import apply_qq_persona
    from app.services import owner_memory,owner_policy,usage_ledger

    out=ROOT/"data/dafeiyu/owner-memory-probe"
    out.mkdir(parents=True,exist_ok=True)
    # Both the owner and message text here are fictional test data. Do not put
    # fixtures into the real archive or pretend the bot actually heard a song.
    owner_policy.POLICY_PATH=out/"fictional-owner.json"
    owner_memory.ARCHIVE_PATH=out/"fictional-memory.sqlite3"
    usage_ledger.LEDGER_PATH=out/"probe-usage.sqlite3"
    owner_policy.POLICY_PATH.write_text(json.dumps({"enabled":True,"owner_id":"12345","owner_name":"久"}),encoding="utf-8")
    scope="isolated-memory-probe:FriendMessage:12345"
    def event(text,mid):
        extras={}
        return NS(unified_msg_origin=scope,get_platform_name=lambda:"aiocqhttp",get_group_id=lambda:"",
            get_message_str=lambda:text,get_extra=extras.get,set_extra=extras.__setitem__,message_obj=NS(raw_message={
                "post_type":"message","message_type":"private","self_id":"99999","user_id":"12345",
                "message_id":mid,"sender":{"nickname":"久"},"message":[{"type":"text","data":{"text":text}}]}))
    seed=event("我给Switch OLED买的是256GB内存卡，用来存游戏，不是在给电脑装内存条", "fixture-source-1")
    owner_memory.record_incoming(seed)
    current=event("鲸鱼娘，我之前说给什么设备买什么卡来着？", "fixture-question-1")
    owner_memory.record_incoming(current)
    request=ProviderRequest(prompt=current.get_message_str(),system_prompt="")
    apply_qq_persona(NS(),current,request)
    await prepare(current,request)
    tools=ToolSet()
    tools.add_tool(FunctionTool(name="qqchat_recall",description="按关键词检索本会话主人的原文，有来源。回忆以前的事先检索。",parameters={
        "type":"object","properties":{"query":{"type":"string"}},"required":["query"]}))
    profile=json.loads((ROOT/"data/astrbot/instance/data/config/abconf_qqchat_dafeiyu.json").read_text(encoding="utf-8-sig"))
    provider_id=profile["agent_runner"]["config"]["model"]["provider_id"]
    if provider_id!="qqchat-chatgpt-luna":
        raise RuntimeError("QQ provider is not the approved Luna")
    selected=next(p for p in profile["provider"] if p["id"]==provider_id)
    provider=register_chatgpt_provider()(selected,profile["provider_settings"])
    calls=[]
    try:
        response=await asyncio.wait_for(provider.text_chat(prompt=request.prompt,system_prompt=request.system_prompt,func_tool=tools),90)
        if response.tools_call_name:
            segments=[]
            specs=[]
            for name,args,cid in zip(response.tools_call_name,response.tools_call_args,response.tools_call_ids):
                if name!="qqchat_recall":
                    raise RuntimeError("Unexpected tool in isolated probe")
                result=recall(current,args.get("query",""))
                calls.append({"tool":name,"found_source":any(r["message_id"]=="fixture-source-1" for r in result["matches"])})
                specs.append({"id":cid,"type":"function","function":{"name":name,"arguments":json.dumps(args,ensure_ascii=False)}})
                segments.append(ToolCallMessageSegment(tool_call_id=cid,content=json.dumps(result,ensure_ascii=False)))
            response=await asyncio.wait_for(provider.text_chat(prompt=None,system_prompt=request.system_prompt,
                contexts=[{"role":"user","content":request.prompt}],func_tool=tools,
                tool_calls_result=ToolCallsResult(tool_calls_info=AssistantMessageSegment(tool_calls=specs),tool_calls_result=segments)),90)
        text=response.completion_text
        passed=bool(calls and all(c["found_source"] for c in calls) and "Switch" in text and "256" in text and "内存卡" in text)
        report={"provider_id":provider_id,"model":provider.get_model(),"synthetic_records_only":True,"posted_to_qq":False,
            "tool_calls":calls,"text":text,"passed":passed,"actual_usage":usage_ledger.ledger().report(owner_only=False),
            "large_context_exercised":False}
        (ROOT/"data/dafeiyu/owner-memory-live-model-verification.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False),flush=True)
        if not passed:
            raise RuntimeError("Luna source recall probe did not pass")
    finally:
        await provider.terminate()


if __name__=="__main__":
    asyncio.run(main())
