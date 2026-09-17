import pytest

from app.core.rate_limit import LocalRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter():
    limiter = LocalRateLimiter(limit=2, window_seconds=60)
    assert await limiter.allow("u")
    assert await limiter.allow("u")
    assert not await limiter.allow("u")


@pytest.mark.asyncio
async def test_group_and_user_keys_are_independent():
    limiter = LocalRateLimiter(limit=1, window_seconds=60)
    assert await limiter.allow("user:1")
    assert await limiter.allow("group:1")
    assert not await limiter.allow("user:1")
    assert not await limiter.allow("group:1")
