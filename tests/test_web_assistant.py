from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import astrbot_web_assistant as web


@pytest.mark.asyncio
async def test_private_browser_followups_close_and_owner_isolation(monkeypatch):
    tool = web.WebAssistant()
    key = ("qq","bot","","owner")
    assert not tool.interested(key,"别打开网页 https://example.org",True)
    assert tool.interested(key,"网页助手，帮我看看这个链接 https://example.org",True)
    event = SimpleNamespace(send=AsyncMock(), plain_result=lambda s:s)
    tab = SimpleNamespace(snapshot=AsyncMock(return_value="页面一"), close=AsyncMock(), click=AsyncMock(return_value="详情"),
                          fill=AsyncMock(return_value="已填好"), link_destination=AsyncMock(return_value=None),
                          page=SimpleNamespace(evaluate=AsyncMock()))
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


@pytest.mark.asyncio
async def test_explicit_external_navigation_and_questions_use_only_rendered_prose(monkeypatch):
    import json
    first = SimpleNamespace(snapshot=AsyncMock(return_value="第一页"),close=AsyncMock(),
                            link_destination=AsyncMock(return_value="https://other.example/notice"))
    other = SimpleNamespace(snapshot=AsyncMock(return_value="公告页"),close=AsyncMock(),
                            reading=AsyncMock(return_value=("公告","比赛报名截至3月8日。")))
    monkeypatch.setattr(web.BrowserSession,"open",AsyncMock(side_effect=[first,other]))
    model = AsyncMock(return_value=SimpleNamespace(completion_text="报名截至3月8日。"))
    brain = AsyncMock(return_value=SimpleNamespace(text_chat=model))
    tool = web.WebAssistant()
    try:
        await tool.command("a","打开网页 https://example.org")
        assert await tool.command("a","点击公告")== "公告页"
        first.close.assert_awaited_once()
        assert "3月8日" in await tool.command("a","问网页：什么时候截止？",brain=brain)
        assert json.loads(model.await_args.kwargs["prompt"])["网页正文"]=="比赛报名截至3月8日。"
        assert "func_tool" not in model.await_args.kwargs and not model.await_args.kwargs["contexts"]
        assert "先把" in await tool.command("b","总结当前网页",brain=brain)
        assert "原文" in await tool.command("a","总结当前网页")
    finally:
        await tool.close()


@pytest.mark.asyncio
async def test_visible_link_follows_canonical_public_redirect_in_real_browser(monkeypatch):
    import httpx
    visits = []
    async def site(request):
        host = request.headers["host"]
        visits.append((host, request.url.path))
        if host == "public.example":
            return httpx.Response(301, headers={"location":"https://www.public.example/detail"})
        return httpx.Response(200, headers={"content-type":"text/html; charset=utf-8"},
                              content="<title>公告详情</title><main>报名截至三月八日</main>".encode())
    make_client = httpx.AsyncClient
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **opts: make_client(transport=httpx.MockTransport(site), **opts))
    pin = AsyncMock(return_value="93.184.216.34")
    monkeypatch.setattr(web, "browser_address", pin)
    tab = await web.BrowserSession.open("https://public.example/go")
    try:
        assert tab.page.url == "https://www.public.example/detail"
        assert "报名截至三月八日" in await tab.snapshot()
        assert ("public.example","/go") in visits and ("www.public.example","/detail") in visits
        assert [call.args[0] for call in pin.await_args_list] == ["https://public.example/go", "https://www.public.example/detail"]
    finally:
        await tab.close()
