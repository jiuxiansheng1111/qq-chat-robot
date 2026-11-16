from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import astrbot_web_assistant as web


@pytest.mark.asyncio
async def test_private_browser_followups_close_and_owner_isolation(monkeypatch):
    tool = web.WebAssistant()
    key = ("qq","bot","","owner")
    event = SimpleNamespace(send=AsyncMock(), plain_result=lambda s:s)
    tab = SimpleNamespace(snapshot=AsyncMock(return_value="页面一"), close=AsyncMock(), click=AsyncMock(return_value="详情"),
                          fill=AsyncMock(return_value="已填好"), page=SimpleNamespace(evaluate=AsyncMock()))
    open_page = AsyncMock(return_value=tab)
    monkeypatch.setattr(web.BrowserSession,"open",open_page)
    monkeypatch.setattr(web,"is_owner_event",lambda e:True)
    assert await tool.handle(None,event,key,"打开网页 https://example.org")
    open_page.assert_awaited_once_with("https://example.org")
    assert tool.interested(key, "点击详情", True)
    assert await tool.handle(None,event,key,"点击详情")
    tab.click.assert_awaited_once_with("详情")
    assert await tool.handle(None,event,key,"在网页的搜索框里输入鲨鱼")
    tab.fill.assert_awaited_once_with("搜索框","鲨鱼")
    assert not await tool.handle(None,event,("qq","bot","group","owner"),"读当前页面")
    assert await tool.handle(None,event,key,"关闭网页助手")
    tab.close.assert_awaited_once()
    assert not tool.tabs and not tool.timers


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1:6190", "file:///C:/Windows", "https://name:pw@example.org"])
async def test_private_destinations_never_open_a_browser(url):
    with pytest.raises(ValueError):
        await web.BrowserSession.open(url)
