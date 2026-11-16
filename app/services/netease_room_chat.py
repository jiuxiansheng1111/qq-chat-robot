"""Bounded authenticated client for the currently joined NetEase room's text chat."""

from __future__ import annotations

import json
import re

import httpx

from app.services.bounded_http import request_bytes
from app.services.netease_member import NeteaseMemberError, _bridge_connection


class NeteaseRoomChatError(RuntimeError):
    """A room chat operation was not confirmed; never retry a send automatically."""


async def room_chat(settings, action="status", *, room_id="", text="", cursor=0):
    if action not in {"connect", "status", "messages", "send", "disconnect"}:
        raise ValueError("无效的一起听留言操作")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", str(room_id)):
        raise ValueError("当前一起听房间无效，请先发邀请加入")
    if action == "send" and (not isinstance(text,str) or not text.strip() or len(text)>500):
        raise ValueError("一起听留言需要1到500个字符")
    if isinstance(cursor,bool) or not isinstance(cursor,int) or cursor<0:
        raise ValueError("一起听留言游标无效")
    try:
        url,token=_bridge_connection(settings)
    except NeteaseMemberError as exc:
        raise NeteaseRoomChatError(str(exc)) from exc
    payload={"action":action,"room_id":room_id}
    if action == "send":
        payload["text"]=text.strip()
    if action == "messages":
        payload["cursor"]=cursor
    async with httpx.AsyncClient(trust_env=False) as client:
        try:
            body=await request_bytes(client,"POST",url+"/api/listen/chat",
                headers={"Authorization":"Bearer "+token},json=payload,
                max_bytes=64*1024,timeout_seconds=65)
            data=json.loads(body)
        except (httpx.HTTPError,TimeoutError,ValueError) as exc:
            raise NeteaseRoomChatError("一起听留言接口暂时没能确认操作；若刚才在发送，先查看房间，避免重复发送") from exc
    if not isinstance(data,dict) or data.get("provider")!="netease_chat" or data.get("code")!=200:
        raise NeteaseRoomChatError("一起听留言接口没有给出有效确认")
    if data.get("room_id")!=room_id:
        raise NeteaseRoomChatError("一起听留言房间不匹配，未转发到其他房间")
    result={"status":str(data.get("status","unknown"))[:80],"success":data.get("success") is True,
            "connected":data.get("connected") is True,"room_id":room_id,"cursor":0,"messages":[]}
    if isinstance(data.get("cursor"),int) and not isinstance(data["cursor"],bool) and data["cursor"]>=0:
        result["cursor"]=data["cursor"]
    if isinstance(data.get("message"),str):
        result["message"]=data["message"][:500]
    if isinstance(data.get("message_id"),str):
        result["message_id"]=data["message_id"][:160]
    messages=data.get("messages",[])
    for item in messages[-50:] if isinstance(messages,list) else []:
        if not isinstance(item,dict) or not isinstance(item.get("text"),str):
            continue
        message_id=str(item.get("message_id", ""))[:160]
        if not message_id:
            continue
        result["messages"].append({"message_id":message_id,"text":item["text"][:500],
            "own":item.get("own") is not False,
            "cursor":item.get("cursor") if isinstance(item.get("cursor"),int) else 0,
            "received_at":item.get("received_at") if isinstance(item.get("received_at"),int) else 0})
    if action == "send" and result["success"] and result["status"]!="sent":
        raise NeteaseRoomChatError("一起听留言没有返回发送确认，暂时不能说已发出")
    return result
