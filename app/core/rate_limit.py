import asyncio
import time
from collections import defaultdict, deque


class LocalRateLimiter:
    def __init__(self, limit: int = 5, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        async with self.lock:
            now = time.monotonic()
            events = self.events[key]
            while events and now - events[0] > self.window_seconds:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


class RedisRateLimiter:
    def __init__(self, redis_url: str, limit: int = 5, window_seconds: int = 60):
        self.redis_url = redis_url
        self.limit = limit
        self.window_seconds = window_seconds
        self.client = None

    async def connect(self) -> None:
        from redis.asyncio import Redis
        self.client = Redis.from_url(self.redis_url, decode_responses=True)
        await self.client.ping()

    async def allow(self, key: str) -> bool:
        if self.client is None:
            raise RuntimeError("redis limiter is not connected")
        redis_key = f"qqchat:rate:{key}"
        count = await self.client.incr(redis_key)
        if count == 1:
            await self.client.expire(redis_key, self.window_seconds)
        return count <= self.limit
