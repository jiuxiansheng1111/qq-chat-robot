import asyncio

import httpx
import pytest

from app.config import Settings
from app.services import http_routing as routing


@pytest.fixture(autouse=True)
def isolate_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(routing, "getproxies", dict)
    monkeypatch.setattr(routing, "_routing_settings", None)
    routing.reset_proxy_cache_for_tests()
    yield
    routing.reset_proxy_cache_for_tests()


def install_transports(monkeypatch, state):
    calls = []
    pools = []

    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, *, proxy=None, **kwargs):
            self.proxy = proxy
            self.closed = False
            assert kwargs["trust_env"] is False
            pools.append(self)

        async def handle_async_request(self, request):
            route = "proxy" if self.proxy else "direct"
            calls.append((route, request.method, await request.aread()))
            error = state.get("proxy_error") if self.proxy else None
            if error:
                raise error("synthetic failure", request=request)
            return httpx.Response(state.get("status", 200), text=route)

        async def aclose(self):
            self.closed = True

    async def probe(_url):
        return state.get("alive", True)

    monkeypatch.setattr(routing.httpx, "AsyncHTTPTransport", Transport)
    monkeypatch.setattr(routing, "_proxy_port_open", probe)
    return calls, pools


async def test_persistent_client_survives_proxy_close_reopen_and_concurrent_requests(monkeypatch):
    state = {"alive": True}
    calls, pools = install_transports(monkeypatch, state)
    clock = [100.0]
    monkeypatch.setattr(routing.time, "monotonic", lambda: clock[0])
    settings = Settings(_env_file=None, web_proxy_url="http://127.0.0.1:7897")
    async with routing.outbound_http_client(settings=settings) as client:
        for alive, expected in [(True, "proxy"), (False, "direct"), (True, "proxy")]:
            state["alive"] = alive
            clock[0] += 6
            responses = await asyncio.gather(*(
                client.post("https://model.invalid/chat", json={"message": "hello"})
                for _ in range(8)
            ))
            assert {response.text for response in responses} == {expected}
    assert len(calls) == 24
    assert len(pools) == 2
    assert all(pool.closed for pool in pools)


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError])
async def test_proxy_fails_after_probe_and_json_post_falls_back_once(monkeypatch, error):
    calls, pools = install_transports(monkeypatch, {"proxy_error": error})
    settings = Settings(_env_file=None, web_proxy_url="http://127.0.0.1:7897")
    async with routing.outbound_http_client(settings=settings) as client:
        result = await client.post("https://model.invalid/chat", json={"prompt": "你好"})
    assert result.text == "direct"
    assert [call[0] for call in calls] == ["proxy", "direct"]
    assert calls[0][2] == calls[1][2]
    assert all(pool.closed for pool in pools)


async def test_response_timeout_does_not_repeat_a_model_request(monkeypatch):
    calls, _ = install_transports(monkeypatch, {"proxy_error": httpx.ReadTimeout})
    settings = Settings(_env_file=None, web_proxy_url="http://127.0.0.1:7897")
    async with routing.outbound_http_client(settings=settings) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.post("https://model.invalid/chat", json={"prompt": "hello"})
    assert [call[0] for call in calls] == ["proxy"]


async def test_http_failure_does_not_repeat_a_model_request(monkeypatch):
    calls, _ = install_transports(monkeypatch, {"status": 503})
    settings = Settings(_env_file=None, web_proxy_url="http://127.0.0.1:7897")
    async with routing.outbound_http_client(settings=settings) as client:
        result = await client.post("https://model.invalid/chat", json={"prompt": "hello"})
    assert result.status_code == 503
    assert [call[0] for call in calls] == ["proxy"]


async def test_closed_environment_proxy_is_not_inherited_again(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    calls, _ = install_transports(monkeypatch, {"alive": False})
    async with routing.outbound_http_client(settings=Settings(_env_file=None)) as client:
        result = await client.get("https://model.invalid/models")
    assert result.text == "direct"
    assert [call[0] for call in calls] == ["direct"]


async def test_initially_closed_auto_proxy_can_be_started_later(monkeypatch):
    state = {"alive": False}
    calls, _ = install_transports(monkeypatch, state)
    clock = [100.0]
    monkeypatch.setattr(routing.time, "monotonic", lambda: clock[0])
    async with routing.outbound_http_client(settings=Settings(_env_file=None)) as client:
        assert (await client.get("https://model.invalid/models")).text == "direct"
        state["alive"] = True
        clock[0] += 6
        assert (await client.get("https://model.invalid/models")).text == "proxy"
    assert [call[0] for call in calls] == ["direct", "proxy"]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]", "host.docker.internal"])
async def test_local_services_always_bypass_proxy(monkeypatch, host):
    calls, _ = install_transports(monkeypatch, {"proxy_error": httpx.ConnectError})
    settings = Settings(_env_file=None, web_proxy_url="http://127.0.0.1:7897")
    async with routing.outbound_http_client(settings=settings) as client:
        assert (await client.get(f"http://{host}:3010/health")).text == "direct"
    assert [call[0] for call in calls] == ["direct"]


async def test_startup_does_not_install_a_process_wide_proxy(monkeypatch):
    install_transports(monkeypatch, {"alive": True})
    await routing.configure_outbound_http(Settings(_env_file=None))
    import os

    assert all(name not in os.environ for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy"))


async def test_existing_client_tracks_system_proxy_changes(monkeypatch):
    calls, _ = install_transports(monkeypatch, {})
    system_proxy = {}
    monkeypatch.setattr(routing, "getproxies", lambda: system_proxy.copy())
    settings = Settings(_env_file=None, web_proxy_auto_detect=False)
    async with routing.outbound_http_client(settings=settings) as client:
        assert (await client.get("https://model.invalid/models")).text == "direct"
        system_proxy["https"] = "http://127.0.0.1:7897"
        assert (await client.get("https://model.invalid/models")).text == "proxy"
        system_proxy.clear()
        assert (await client.get("https://model.invalid/models")).text == "direct"
    assert [call[0] for call in calls] == ["direct", "proxy", "direct"]


async def test_stalled_probe_close_does_not_block_next_route(monkeypatch):
    class Writer:
        def __init__(self):
            self.transport = self
            self.closed = False
            self.aborted = False

        def close(self):
            self.closed = True

        async def wait_closed(self):
            await asyncio.Event().wait()

        def abort(self):
            self.aborted = True

    writer = Writer()

    async def connect(_host, _port):
        return None, writer

    monkeypatch.setattr(routing.asyncio, "open_connection", connect)
    assert await asyncio.wait_for(
        routing._proxy_port_open("http://127.0.0.1:7897", timeout=0.01), timeout=1,
    )
    assert writer.closed and writer.aborted
    # 同一循环仍可探测下一条线路，不留一个无限等待关闭的任务。
    assert await asyncio.wait_for(
        routing._proxy_port_open("http://127.0.0.1:7890", timeout=0.01), timeout=1,
    )


async def test_real_sockets_same_client_survives_proxy_shutdown_and_restart(monkeypatch):
    async def reply(reader, writer, body):
        try:
            try:
                await reader.readuntil(b"\r\n\r\n")
            except asyncio.IncompleteReadError:
                return
            writer.write(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                + str(len(body)).encode() + b"\r\n\r\n" + body,
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def direct_handler(reader, writer):
        await reply(reader, writer, b"direct")

    async def proxy_handler(reader, writer):
        await reply(reader, writer, b"proxy")

    direct = await asyncio.start_server(direct_handler, "127.0.0.1", 0)
    proxy = await asyncio.start_server(proxy_handler, "127.0.0.1", 0)
    proxy_port = proxy.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{direct.sockets[0].getsockname()[1]}/check"
    settings = Settings(_env_file=None, web_proxy_url=f"http://127.0.0.1:{proxy_port}")
    # 此用例用本机 HTTP 服务代替外部网站；生产中本机服务始终直连。
    monkeypatch.setattr(routing, "_local_host", lambda _: False)
    try:
        async with routing.outbound_http_client(settings=settings, timeout=2) as client:
            assert (await client.get(url)).text == "proxy"
            proxy.close()
            await proxy.wait_closed()
            assert (await client.get(url)).text == "direct"
            proxy = await asyncio.start_server(proxy_handler, "127.0.0.1", proxy_port)
            routing.reset_proxy_cache_for_tests()
            assert (await client.get(url)).text == "proxy"
    finally:
        direct.close()
        proxy.close()
        await direct.wait_closed()
        await proxy.wait_closed()
