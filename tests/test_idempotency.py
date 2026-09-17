import asyncio

from app.core.idempotency import EventDeduplicator


class FakeRedis:
    def __init__(self):
        self.keys = set()

    async def set(self, key, value, ex, nx):
        if nx and key in self.keys:
            return False
        self.keys.add(key)
        return True


async def test_duplicate_event_is_ignored():
    deduplicator = EventDeduplicator(ttl_seconds=60)
    assert await deduplicator.first_seen("message-1")
    assert not await deduplicator.first_seen("message-1")
    assert await deduplicator.first_seen("message-2")


async def test_concurrent_duplicate_event_is_safe():
    deduplicator = EventDeduplicator(ttl_seconds=60)
    results = await asyncio.gather(*(deduplicator.first_seen("same") for _ in range(10)))
    assert sum(results) == 1


async def test_redis_deduplicator_uses_atomic_set():
    from app.core.idempotency import RedisEventDeduplicator

    deduplicator = RedisEventDeduplicator("redis://unused")
    deduplicator.client = FakeRedis()
    assert await deduplicator.first_seen("redis-event")
    assert not await deduplicator.first_seen("redis-event")
