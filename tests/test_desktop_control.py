from types import SimpleNamespace
from unittest.mock import AsyncMock

from app import astrbot_desktop_control as desktop


async def test_owner_action_once_and_group_exclusion(tmp_path, monkeypatch):
    plugin = desktop.DesktopControl()
    host = SimpleNamespace(runtime=SimpleNamespace(project_root=tmp_path))
    key = ("qq","bot","","owner")
    raw = {"message_type":"private","self_id":"bot","user_id":"owner","message_id":12}
    event = SimpleNamespace(get_platform_id=lambda:"qq", message_obj=SimpleNamespace(raw_message=raw),
                            send=AsyncMock(), plain_result=lambda s:s)
    monkeypatch.setattr(desktop, "is_owner_event", lambda evt: True)
    calls = []
    monkeypatch.setattr(desktop, "windows_action", lambda name: calls.append(name) or "已请求")
    for _ in range(2):
        assert await plugin.handle(host, event, key, "帮我在电脑上打开计算器")
    assert calls == ["计算器"]
    assert not plugin.interested(key, "在电脑上打开任意程序", True)
    assert not plugin.interested(key, "不要让电脑锁屏", True)
    assert not await plugin.handle(host, event, ("qq","bot","group","owner"), "让电脑锁屏")
