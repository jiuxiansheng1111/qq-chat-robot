from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import astrbot_phone_control as phone
from tests.activity_fixtures import ActivityEvent, host_at


@pytest.mark.asyncio
async def test_private_termux_actions_select_fixed_commands_and_deduplicate(tmp_path,monkeypatch):
    tool, host = phone.PhoneControl(), host_at(tmp_path)
    key = ("qq","99","","11")
    cmds = tmp_path/"termux-bin"
    cmds.mkdir()
    for command in ("termux-battery-status","termux-vibrate","termux-torch"): (cmds/command).touch()
    monkeypatch.setenv("QQCHAT_TERMUX_BIN",str(cmds))
    monkeypatch.setattr(phone,"is_owner_event",lambda ev: ev.message_obj.raw_message["user_id"]=="11")
    proc = Mock(return_value=SimpleNamespace(returncode=0,stdout=b'{"percentage":68,"status":"CHARGING"}'))
    monkeypatch.setattr(phone.subprocess,"run",proc)
    e = ActivityEvent(key,"1")
    assert await tool.handle(host,e,key,"手机还剩多少电")
    assert "68%，正在充电" in e.send.await_args.args[0]
    assert proc.call_args.args[0]==[str(cmds/"termux-battery-status")]
    proc.return_value = SimpleNamespace(returncode=0,stdout=b"")
    e = ActivityEvent(key,"2")
    assert await tool.handle(host,e,key,"帮我关闭手机的手电筒")
    assert proc.call_args.args[0]==[str(cmds/"termux-torch"),"off"]
    assert await phone.PhoneControl().handle(host,e,key,"帮我关闭手机的手电筒")
    assert proc.call_count==2
    group = ("qq","99","22","11")
    assert not await tool.handle(host,ActivityEvent(group,"3"),group,"让手机震动一下")
    other = (*key[:3],"12")
    assert not await tool.handle(host,ActivityEvent(other,"4"),other,"打开手机手电筒")
    assert not tool.interested(key,"别打开手机手电筒",True)
    proc.return_value = SimpleNamespace(returncode=0,stdout=b'{"API_ERROR":"bad"}')
    assert "错误" in phone.termux_action("vibrate")
    monkeypatch.delenv("QQCHAT_TERMUX_BIN")
    assert "未连接" in phone.termux_action("battery")
