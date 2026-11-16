from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types


@pytest.mark.asyncio
async def test_restored_route_uses_real_native_message_chain_and_exact_original_session(monkeypatch,tmp_path):
    root=_find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT",str(tmp_path/"isolated-astrbot"))
    types=_load_astrbot_types(root,monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    from app.services.listening_operations import RestoredListeningEvent

    context=NS(get_platform_inst=lambda _:NS(meta=lambda:NS(name="aiocqhttp")),send_message=AsyncMock(return_value=True))
    owner=("qq-local:GroupMessage:12345","23456")
    e=RestoredListeningEvent(context,owner)
    result=e.plain_result("上次分析中断的真实终态")
    assert len(result.chain)==1 and isinstance(result.chain[0],types.Plain)
    await e.send(result)
    assert context.send_message.await_args.args==(owner[0],result)
    assert e.message_obj.raw_message["user_id"]==owner[1]
    assert e.get_group_id()=="12345"
    context.send_message.return_value=False
    with pytest.raises(RuntimeError):await e.send(result)


def test_restored_route_rejects_unavailable_or_non_qq_platform():
    from app.services.listening_operations import RestoredListeningEvent

    for platform in [None,NS(meta=lambda:NS(name="weixin_oc"))]:
        with pytest.raises(ValueError):
            RestoredListeningEvent(NS(get_platform_inst=lambda _, value=platform: value),
                ("qq-local:FriendMessage:12345","23456"))
