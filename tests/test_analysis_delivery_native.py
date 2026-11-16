from __future__ import annotations

from unittest.mock import AsyncMock

from test_astrbot_native_onebot import _find_project_root, _load_astrbot_types, _make_group_event


async def test_native_nonempty_marker_has_no_receipt_but_stopped_event_can_send_final_analysis(monkeypatch,tmp_path):
    root=_find_project_root()
    monkeypatch.setenv("ASTRBOT_ROOT",str(tmp_path/"isolated-astrbot"))
    monkeypatch.chdir(tmp_path)
    _load_astrbot_types(root,monkeypatch)
    monkeypatch.syspath_prepend(str(root))
    from astrbot.core.message.message_event_result import ResultContentType
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.provider.entities import LLMResponse

    from app.astrbot_analysis_delivery import (
        defer_analysis_reply,
        defer_model_receipt,
        suppress_model_receipt,
    )
    from app.astrbot_silence import NO_REPLY_MARKER

    e,api,_=_make_group_event(_load_astrbot_types(root,monkeypatch))
    e.message_obj.group_id="12345"
    async def send_group_msg(**params):
        return await api.call_action("send_group_msg",**params)
    e.bot.send_group_msg=send_group_msg
    response=LLMResponse(role="assistant",completion_text="我先听一下，然后再分析")
    defer_analysis_reply(e,"fixture-job")
    assert defer_model_receipt(e,response)
    assert response.completion_text==NO_REPLY_MARKER and response.completion_text
    result=e.plain_result(response.completion_text)
    result.set_result_content_type(ResultContentType.LLM_RESULT)
    e.set_result(result)
    assert suppress_model_receipt(e) and e.is_stopped()
    assert not getattr(e.get_result(),"chain",[])
    assert api.calls==[]
    # Keep the real QQ serializer/transport call; isolate only core history writes.
    monkeypatch.setattr(AstrMessageEvent,"send",AsyncMock())
    await e.send(e.plain_result("这段旋律很舒缓，节奏的留白让人慢慢松下来"))
    assert len(api.calls)==1 and api.calls[0][0]=="send_group_msg"
    encoded=str(api.calls[0][1])
    assert "这段旋律很舒缓" in encoded and NO_REPLY_MARKER not in encoded
