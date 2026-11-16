"""验证实际群报适配器在 NapCat 上不调用不支持的头像资料接口。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_astrbot_report_history import adapter_type as adapter_type  # noqa: PLC0414


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.asyncio
async def test_napcat_missing_avatar_uses_cdn_and_negative_cache(adapter_type, wrapped):
    """普通 NapCat 资料没有头像字段时仍生成有效地址，并避免重复查询。"""
    version = {"app_name": "NapCat"}
    if wrapped:
        version = {"data": version}
    bot = SimpleNamespace(call_action=AsyncMock(side_effect=[version, {"nickname": "test"}]))
    adapter = adapter_type(bot)

    first = await adapter.get_user_avatar_url("100000001", size=100)
    second = await adapter.get_user_avatar_url("100000001", size=640)

    assert first == adapter._build_cdn_avatar_url("100000001", 100)
    assert second == adapter._build_cdn_avatar_url("100000001", 640)
    assert [call.args[0] for call in bot.call_action.await_args_list] == [
        "get_version_info",
        "get_stranger_info",
    ]
    assert bot.call_action.await_args_list[-1].kwargs == {"user_id": 100000001}


@pytest.mark.asyncio
async def test_protocol_avatar_and_positive_cache_are_preserved(adapter_type):
    """协议返回真实头像时保留地址和缓存，不替换成 CDN。"""
    url = "https://example.org/avatar.png"
    bot = SimpleNamespace(
        call_action=AsyncMock(
            side_effect=[
                {"app_name": "NapCat"},
                {"data": {"avatar_url": url}},
            ]
        )
    )
    adapter = adapter_type(bot)
    assert await adapter.get_user_avatar_url("100000001") == url
    assert await adapter.get_user_avatar_url("100000001") == url
    assert bot.call_action.await_count == 2


@pytest.mark.asyncio
async def test_napcat_profile_failure_does_not_probe_unsupported_api(adapter_type):
    """支持的资料接口异常时也不能转而调用不支持的接口。"""
    bot = SimpleNamespace(
        call_action=AsyncMock(
            side_effect=[
                {"app_name": "NapCat"},
                RuntimeError("temporary failure"),
            ]
        )
    )
    adapter = adapter_type(bot)
    assert await adapter.get_user_avatar_url("100000001") == (
        adapter._build_cdn_avatar_url("100000001", 100)
    )
    assert [call.args[0] for call in bot.call_action.await_args_list] == [
        "get_version_info",
        "get_stranger_info",
    ]


@pytest.mark.asyncio
async def test_other_bridge_keeps_extended_profile_fallback(adapter_type):
    """其他桥接协议仍保留原来的扩展头像查询能力。"""
    url = "https://example.org/bridge-avatar.png"
    bot = SimpleNamespace(
        call_action=AsyncMock(
            side_effect=[
                {"app_name": "OtherOneBot"},
                {},
                {"avatar": url},
            ]
        )
    )
    assert await adapter_type(bot).get_user_avatar_url("mapped-user") == url
    assert [call.args[0] for call in bot.call_action.await_args_list] == [
        "get_version_info",
        "get_stranger_info",
        "get_user_info",
    ]
    assert bot.call_action.await_args_list[-1].kwargs == {"user_id": "mapped-user"}


@pytest.mark.asyncio
async def test_concurrent_avatar_requests_share_one_protocol_lookup(adapter_type):
    """同一用户并发渲染多个栏目时只查询一次头像。"""
    bot = SimpleNamespace(call_action=AsyncMock(side_effect=[{"app_name": "NapCat"}, {}]))
    adapter = adapter_type(bot)
    urls = await asyncio.gather(*(adapter.get_user_avatar_url("100000001") for _ in range(5)))
    assert urls == [adapter._build_cdn_avatar_url("100000001", 100)] * 5
    assert bot.call_action.await_count == 2


@pytest.mark.asyncio
async def test_slow_driver_detection_is_bounded_and_uses_standard_api(adapter_type):
    """协议探测超时不阻塞群报，也不盲试扩展接口。"""

    async def call_action(action, **params):
        if action == "get_version_info":
            await asyncio.sleep(10)
        assert action == "get_stranger_info"
        return {}

    bot = SimpleNamespace(call_action=AsyncMock(side_effect=call_action))
    adapter = adapter_type(bot)
    adapter.AVATAR_FETCH_TIMEOUT = 0.01
    url = await asyncio.wait_for(adapter.get_user_avatar_url("100000001"), timeout=1)
    assert url == adapter._build_cdn_avatar_url("100000001", 100)
    assert [call.args[0] for call in bot.call_action.await_args_list] == [
        "get_version_info",
        "get_stranger_info",
    ]
