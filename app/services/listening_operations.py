"""Short-lived listening operation metadata, never chat text, tokens or audio contents."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import time
import uuid
from pathlib import Path

_SCOPE=re.compile(r"[A-Za-z0-9_-]{1,80}:(?:FriendMessage|GroupMessage):[0-9]{1,20}")
_ID=re.compile(r"[0-9]{1,20}")
_ROOM=re.compile(r"[A-Za-z0-9_-]{1,128}")
_JOB=re.compile(r"[a-f0-9]{12}")
_DIGEST=re.compile(r"[a-f0-9]{64}")


def valid_owner(owner):
    if not (isinstance(owner,(list,tuple)) and len(owner)==2
            and _SCOPE.fullmatch(str(owner[0])) and _ID.fullmatch(str(owner[1]))):return False
    _,kind,peer=str(owner[0]).split(":",2)
    return kind!="FriendMessage" or peer==str(owner[1])


class ListeningOperations:
    def __init__(self,path:Path,*,clock=time.time):
        self.path=Path(path)
        self.clock=clock
        self.data={"version":1,"room":None,"analysis":[]}
        self.corrupt=False
        self.needs_rewrite=False
        if self.path.is_file():
            try:
                if self.path.stat().st_size>65536:
                    raise ValueError("operation metadata too large")
                raw=json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw,dict) or raw.get("version")!=1:
                    raise ValueError("unknown operation metadata")
                self.data["room"]=self._room(raw.get("room"))
                for item in raw.get("analysis",[])[:32]:
                    job=self._job(item)
                    if job:self.data["analysis"].append(job)
                self.needs_rewrite=raw!=self.data
            except (OSError,ValueError,TypeError,AttributeError):
                self.corrupt=True
                self.data={"version":1,"room":None,"analysis":[]}

    def _expiry(self,value,maximum):
        return (isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
                and self.clock()<value<=self.clock()+maximum+300)

    def _room(self,item):
        if not isinstance(item,dict) or not valid_owner(item.get("owner")):
            return None
        if not _ROOM.fullmatch(str(item.get("room_id",""))) or not self._expiry(item.get("expires_at"),7200):
            return None
        song=str(item.get("song_id",""))
        cursor=item.get("chat_cursor",0)
        return {"owner":[str(value) for value in item["owner"]],"room_id":item["room_id"],"song_id":song if _ID.fullmatch(song) else "",
                "expires_at":item["expires_at"],
                "seen_songs":[str(value) for value in item.get("seen_songs",[])[:64] if _ID.fullmatch(str(value))],
                "pending_songs":[str(value) for value in item.get("pending_songs",[])[:64] if _ID.fullmatch(str(value))],
                "chat_cursor":cursor if isinstance(cursor,int) and not isinstance(cursor,bool) and cursor>=0 else 0,
                "chat_initialized":item.get("chat_initialized") is True,
                "chat_seen":[value for value in item.get("chat_seen",[])[:200] if isinstance(value,str) and _DIGEST.fullmatch(value)]}

    def _job(self,item):
        if not isinstance(item,dict) or not valid_owner(item.get("owner")) or not _JOB.fullmatch(str(item.get("job_id",""))):
            return None
        if not self._expiry(item.get("expires_at"),21600):return None
        status=item.get("status")
        if status not in {"accepted","interrupted","terminal_ready","delivery_unknown"}:return None
        room=str(item.get("room_id",""))
        song=str(item.get("song_id",""))
        return {"job_id":item["job_id"],"owner":[str(value) for value in item["owner"]],"status":status,"song_id":song if _ID.fullmatch(song) else "",
                "room_id":room if _ROOM.fullmatch(room) else "","expires_at":item["expires_at"]}

    def _save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_name(self.path.name+"."+uuid.uuid4().hex+".tmp")
        try:
            temporary.write_text(json.dumps(self.data,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
            for attempt in range(4):
                try:
                    os.replace(temporary,self.path)
                    self.needs_rewrite=False
                    break
                except PermissionError:
                    if attempt==3:raise
                    time.sleep(.015*(attempt+1))
        finally:
            temporary.unlink(missing_ok=True)

    def room(self):
        return copy.deepcopy(self._room(self.data.get("room")))

    def purge(self):
        if self.corrupt:return
        current={"version":1,"room":self._room(self.data.get("room")),
                 "analysis":[item for value in self.data["analysis"] if (item:=self._job(value))]}
        if current!=self.data or self.needs_rewrite:
            self.data=current
            self._save()

    def save_room(self,owner,room_id,*,expires_at,song_id="",seen_songs=(),pending_songs=(),chat_cursor=0,chat_seen=(),chat_initialized=False):
        item=self._room({"owner":owner,"room_id":room_id,"expires_at":expires_at,"song_id":song_id,
            "seen_songs":list(seen_songs),"pending_songs":list(pending_songs),"chat_cursor":chat_cursor,"chat_seen":list(chat_seen),"chat_initialized":chat_initialized})
        if item is None:return False
        self.data["room"]=item
        self._save()
        return True

    def clear_room(self,owner=None,room_id=None):
        current=self.data.get("room")
        if current and (owner is None or current["owner"]==list(owner)) and (room_id is None or current["room_id"]==room_id):
            self.data["room"]=None
            self._save()

    def start(self,job_id,owner,*,room_id="",song_id=""):
        item=self._job({"job_id":job_id,"owner":owner,"room_id":room_id,"song_id":song_id,"status":"accepted","expires_at":self.clock()+21600})
        if item is None:return False
        self.data["analysis"]=[x for x in self.data["analysis"] if x["job_id"]!=job_id and self._job(x)][-31:]+[item]
        self._save()
        return True

    def update(self,job_id,status):
        for item in self.data["analysis"]:
            if item["job_id"]==job_id:
                item["status"]=status
                if self._job(item) is None:raise ValueError("invalid analysis operation status")
                self._save()
                return

    def finish(self,job_id):
        before=len(self.data["analysis"])
        self.data["analysis"]=[item for item in self.data["analysis"] if item["job_id"]!=job_id]
        if before!=len(self.data["analysis"]):self._save()

    def interrupted(self):
        return [copy.deepcopy(item) for item in self.data["analysis"] if self._job(item)]


class RestoredListeningEvent:
    """A validated existing QQ route, with no reconstructed chat or user instructions."""

    def __init__(self,context,owner):
        from types import SimpleNamespace

        if not valid_owner(owner):raise ValueError("invalid listening recovery owner")
        platform_id,kind,peer=str(owner[0]).split(":",2)
        platform=context.get_platform_inst(platform_id)
        if platform is None or platform.meta().name not in {"aiocqhttp","onebot","onebot_v11"}:
            raise ValueError("listening recovery platform is unavailable")
        self.context=context
        self.unified_msg_origin=str(owner[0])
        self.platform_id=platform_id
        self.platform_name=platform.meta().name
        self.user_id=str(owner[1])
        self.group_id=peer if kind=="GroupMessage" else ""
        self.extras={}
        self.message_obj=SimpleNamespace(message=[],raw_message={"user_id":self.user_id,
            "message_type":"group" if self.group_id else "private","group_id":self.group_id})
        from app.astrbot_failure_reply import install_send_guard
        install_send_guard(context,self)

    def get_platform_name(self):return self.platform_name
    def get_platform_id(self):return self.platform_id
    def get_group_id(self):return self.group_id
    def get_sender_id(self):return self.user_id
    def get_message_str(self):return "继续已经确认加入的网易云陪听"
    def get_extra(self,key,default=None):return self.extras.get(key,default)
    def set_extra(self,key,value):self.extras[key]=value

    def plain_result(self,text):
        from astrbot.core.message.message_event_result import MessageChain

        return MessageChain().message(text)

    async def send(self,message):
        delivered=await self.context.send_message(self.unified_msg_origin,message)
        if delivered is False:raise RuntimeError("listening recovery delivery route unavailable")
