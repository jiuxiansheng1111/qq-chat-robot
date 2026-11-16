from types import SimpleNamespace
from unittest.mock import AsyncMock


class ActivityEvent:
    def __init__(self,key,mid):
        platform, bot, group, user = key
        raw = {"post_type":"message","self_id":bot,"user_id":user,"message_id":mid,
               "message_type":"group" if group else "private","group_id":group,"sender":{"nickname":user},"message":[]}
        self.message_obj = SimpleNamespace(raw_message=raw, message=[])
        self.platform = platform
        self.unified_msg_origin = platform + (":GroupMessage:"+group if group else ":FriendMessage:"+user)
        self.send = AsyncMock()
        self.extra = {}

    def get_platform_id(self): return self.platform
    def get_platform_name(self): return "aiocqhttp"
    def get_extra(self,key,default=None): return self.extra.get(key,default)
    def plain_result(self,text): return text
    def chain_result(self,chain): return chain


def host_at(root):
    return SimpleNamespace(runtime=SimpleNamespace(project_root=root, _core=SimpleNamespace(settings=None)), context=SimpleNamespace())


async def turn(tool,host,key,mid,text):
    e = ActivityEvent(key,mid)
    assert tool.interested(key,text,True), text
    assert await tool.handle(host,e,key,text)
    return e.send.await_args.args[0] if e.send.await_count else ""
