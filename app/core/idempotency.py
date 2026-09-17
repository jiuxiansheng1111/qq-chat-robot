import asyncio
import time


class EventDeduplicator:
    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._seen: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def first_seen(self, event_id: str) -> bool:
        if not event_id:
            return True
        async with self._lock:
            now = time.monotonic()
            expired = [key for key, timestamp in self._seen.items() if now - timestamp > self.ttl_seconds]
            for key in expired:
                self._seen.pop(key, None)
            if event_id in self._seen:
                return False
            self._seen[event_id] = now
            return True


class RedisEventDeduplicator:
    def __init__(self, redis_url: str, ttl_seconds: int = 300):
        self.redis_url = redis_url
        self.ttl_seconds = ttl_seconds
        self.client = None

    async def connect(self) -> None:
        from redis.asyncio import Redis

        self.client = Redis.from_url(self.redis_url, decode_responses=True)
        await self.client.ping()

    async def first_seen(self, event_id: str) -> bool:
        if not event_id:
            return True
        if self.client is None:
            raise RuntimeError("redis deduplicator is not connected")
        return bool(
            await self.client.set(
                f"qqchat:event:{event_id}", "1", ex=self.ttl_seconds, nx=True
            )
        )
