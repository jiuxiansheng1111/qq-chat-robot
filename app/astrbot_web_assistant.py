"""User-directed browsing in a short-lived, per-conversation Playwright context."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from app.services.local_report_render import _public_asset_url, _resolve_static_images, find_report_browser
from app.services.bounded_http import request_json
from app.services.http_routing import outbound_http_client
from app.services.owner_policy import is_owner_event

_OPEN = re.compile(r"(?:网页助手[，,：: ]*|(?:帮我|请)?(?:打开|浏览|看看|读一下|看一下)(?:这个|这个网页|网页|链接|这个链接)?[：: ]*)\s*(https?://[^\s<>《》]+)", re.I)
_CLICK = re.compile(r"^(?:网页助手[，,：: ]*)?(?:点开|点一下|点击)(?:网页上|页面上|网页里|页面里|网页上的|页面上的)?[「“\"]?(.{1,90}?)[」”\"]?[。！!]?$")
_READ = re.compile(r"^(?:网页助手[，,：: ]*)?(?:读一下这个网页|看看当前网页|读当前页面|网页上有什么|网页往下翻|网页往上翻|关闭网页助手|关闭这个网页)[。？?！!]*$")
_FILL = re.compile(r"^(?:网页助手[，,：: ]*)?(?:在网页的|在页面的)(.{1,50}?)里(?:输入|填入)[「“\"]?(.{1,240}?)[」”\"]?$")


async def browser_address(normalized):
    if not normalized:
        return None
    allowed, rules = await _resolve_static_images({normalized})
    if normalized in allowed:
        return rules[0].split()[-1]
    # An installed fake-IP proxy can map every public hostname to the benchmarking range.
    # Resolve that case via a public DNS resolver, still connect only to a verified public IP.
    host = urlsplit(normalized).hostname
    try:
        async with asyncio.timeout(3):
            local = await asyncio.get_running_loop().getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
        fake_range = ipaddress.ip_network("198.18.0.0/15")
        if not local or not all(ipaddress.ip_address(x[4][0]) in fake_range for x in local):
            return None
        async with outbound_http_client(timeout=6) as client:
            answer = await request_json(client, "GET", "https://dns.google/resolve", params={"name":host,"type":"A"},
                                        max_bytes=65536, timeout_seconds=7)
        addresses = [ipaddress.IPv4Address(record["data"]) for record in answer.get("Answer", []) if record.get("type") == 1]
        if answer.get("Status") == 0 and addresses and all(ip.is_global for ip in addresses):
            return str(addresses[0])
    except (OSError, httpx.HTTPError, ValueError, TimeoutError):
        pass
    return None


class BrowserSession:
    def __init__(self, engine, browser, context, page, client):
        self.engine, self.browser, self.context, self.page, self.client = engine, browser, context, page, client

    @classmethod
    async def open(cls, url):
        normalized = _public_asset_url(url)
        pinned = await browser_address(normalized)
        if not pinned:
            raise ValueError("请给我一个能解析到公网的 HTTP 或 HTTPS 网页地址。")
        from playwright.async_api import async_playwright

        origin = urlsplit(normalized)
        engine = await async_playwright().start()
        client = httpx.AsyncClient(trust_env=False, timeout=12)
        try:
            browser = await engine.chromium.launch(executable_path=find_report_browser(), headless=True,
                proxy={"server":"http://127.0.0.1:1", "bypass":"<-loopback>"},
                args=["--disable-background-networking", "--disable-quic",
                      "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                      "--host-resolver-rules=MAP * ~NOTFOUND"])
            context = await browser.new_context(accept_downloads=False, service_workers="block", locale="zh-CN")
            async def same_site(route):
                await serve_origin(route, origin, pinned, client)
            await context.route("**/*", same_site)
            await context.route_web_socket("**/*", lambda socket: socket.close())
            page = await context.new_page()
            page.set_default_timeout(6000)
            response = await page.goto(normalized, wait_until="domcontentloaded", timeout=18000)
            if not response or not response.ok:
                raise ValueError("这个页面没有成功打开。")
            return cls(engine, browser, context, page, client)
        except BaseException:
            try:
                await engine.stop()
            finally:
                await client.aclose()
            raise

    async def snapshot(self):
        # Return text visible at the current scroll position; page content is never treated as a tool instruction.
        content = await self.page.evaluate("""() => {
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), lines = [];
          let node;
          while ((node = walker.nextNode()) && lines.join(' ').length < 1400) {
            const s = node.textContent.trim(), el = node.parentElement;
            if (!s || !el || el.closest('script,style,noscript,[hidden]')) continue;
            const r = document.createRange(); r.selectNodeContents(node);
            const box = r.getBoundingClientRect();
            if (box.width && box.height && box.bottom > 0 && box.top < innerHeight && getComputedStyle(el).visibility === 'visible') lines.push(s);
          }
          return lines.join(' ').slice(0,1200);
        }""")
        title = (await self.page.title()).strip()
        return (title or "当前网页") + "\n" + (content or "当前可见区域没有可读文字。")

    async def click(self, label):
        matches = self.page.get_by_role("link", name=label, exact=True).or_(self.page.get_by_role("button", name=label, exact=True))
        if await matches.count() != 1:
            return "页面里没有唯一匹配的按钮或链接，告诉我完整的文字吧。"
        await matches.click()
        return await self.snapshot()

    async def fill(self, label, value):
        fields = self.page.get_by_role("textbox", name=label, exact=True)
        if await fields.count() != 1:
            return "没找到唯一匹配的输入框。"
        await fields.fill(value)
        return "已填好「" + label + "」。"

    async def close(self):
        try:
            await self.browser.close()
        finally:
            try:
                await self.engine.stop()
            finally:
                await self.client.aclose()


async def serve_origin(route, origin, pinned, client):
    """Serve only the explicit site's requests, including each redirect, from its verified address."""
    req = route.request
    dest = urlsplit(req.url)
    if (dest.scheme, dest.netloc.casefold()) != (origin.scheme, origin.netloc.casefold()) or req.method not in {"GET","HEAD","POST"}:
        await route.abort()
        return
    body = req.post_data_buffer
    if body and len(body) > 1_000_000:
        await route.abort()
        return
    headers = {k:v for k,v in (await req.all_headers()).items()
               if k in {"accept", "accept-language", "content-type", "user-agent", "cookie", "origin", "referer"}}
    headers.update({"host":origin.netloc, "accept-encoding":"identity"})
    target = httpx.URL(req.url).copy_with(host=pinned)
    try:
        async with asyncio.timeout(15), client.stream(req.method, target, headers=headers, content=body,
                extensions={"sni_hostname":origin.hostname}) as response:
            location = response.headers.get("location")
            if location:
                redirected = urlsplit(urljoin(req.url, location))
                if (redirected.scheme, redirected.netloc.casefold()) != (origin.scheme, origin.netloc.casefold()):
                    await route.abort()
                    return
            payload = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                payload.extend(chunk)
                if len(payload) > 6_000_000:
                    await route.abort()
                    return
            response_headers = {k:v for k,v in response.headers.items() if k not in
                                {"content-encoding","content-length","transfer-encoding","connection","alt-svc","report-to","nel"}}
            await route.fulfill(status=response.status_code, headers=response_headers, body=bytes(payload))
    except (httpx.HTTPError, TimeoutError):
        await route.abort()


class WebAssistant:
    title = "网页助手"

    def __init__(self):
        self.tabs = {}
        self.timers = {}
        self.lock = asyncio.Lock()

    def interested(self, key, text, addressed):
        return bool((addressed or not key[2]) and (_OPEN.search(text) or _READ.fullmatch(text) or _FILL.fullmatch(text)
                                                   or key in self.tabs and _CLICK.fullmatch(text)))

    async def drop(self, key):
        timer = self.timers.pop(key, None)
        if timer:
            timer.cancel()
        tab = self.tabs.pop(key, None)
        if tab:
            await tab.close()

    async def expire(self, key, tab):
        async with self.lock:
            if self.tabs.get(key) is tab and key not in self.timers:
                await self.drop(key)

    def touch(self, key):
        timer = self.timers.pop(key, None)
        if timer:
            timer.cancel()
        tab = self.tabs[key]
        def idle():
            self.timers.pop(key, None)
            asyncio.create_task(self.expire(key, tab))
        self.timers[key] = asyncio.get_running_loop().call_later(600, idle)

    async def close(self):
        async with self.lock:
            for key in list(self.tabs):
                await self.drop(key)

    async def handle(self, host, event, key, text):
        if key[2] or not is_owner_event(event):
            return False
        async with self.lock:
            opened, clicked, fill = _OPEN.search(text), _CLICK.fullmatch(text), _FILL.fullmatch(text)
            try:
                if opened:
                    new = await BrowserSession.open(opened[1].rstrip("，。；！？、）)"))
                    await self.drop(key)
                    self.tabs[key] = new
                    self.touch(key)
                    reply = await new.snapshot()
                elif "关闭" in text:
                    await self.drop(key)
                    reply = "网页已经关了。"
                elif key not in self.tabs:
                    reply = "先把要打开的网页地址发给我吧。"
                else:
                    tab = self.tabs[key]
                    self.touch(key)
                    if clicked:
                        reply = await tab.click(clicked[1])
                    elif fill:
                        reply = await tab.fill(fill[1], fill[2])
                    else:
                        if "翻" in text:
                            await tab.page.evaluate("(n) => window.scrollBy(0,n*window.innerHeight*.8)", -1 if "上翻" in text else 1)
                        reply = await tab.snapshot()
            except ValueError as exc:
                reply = str(exc)
        await event.send(event.plain_result(reply))
        return True
