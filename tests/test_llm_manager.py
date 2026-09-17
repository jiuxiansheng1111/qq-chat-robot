import asyncio

from app.config import Settings
from app.llm.manager import LLMManager


class FakeProvider:
    def __init__(self):
        self.running = 0
        self.max_running = 0

    async def chat(self, messages):
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        await asyncio.sleep(0.02)
        self.running -= 1
        return "ok"


class FailingProvider:
    async def chat(self, messages):
        from app.llm.providers import LLMError

        raise LLMError("429 upstream")


class SuccessfulProvider:
    async def chat(self, messages):
        return "fallback-ok"


async def test_llm_manager_respects_concurrency_limit():
    settings = Settings(
        _env_file=None,
        llm_max_concurrency=2,
        llm_queue_size=10,
        llm_queue_timeout_seconds=2,
    )
    manager = LLMManager(settings)
    provider = FakeProvider()
    manager.provider = provider
    manager.fallback = provider

    results = await asyncio.gather(*(manager.ask([]) for _ in range(10)))

    assert results == ["ok"] * 10
    assert provider.max_running <= 2


async def test_llm_manager_falls_back_and_records_stats():
    manager = LLMManager(Settings(_env_file=None, llm_queue_timeout_seconds=1))
    manager.provider = FailingProvider()
    manager.fallback = SuccessfulProvider()

    assert await manager.ask([]) == "fallback-ok"
    snapshot = manager.snapshot()
    assert snapshot["zhipu"]["failures"] == 1
    assert snapshot["zhipu"]["rate_limited"] == 1
    assert snapshot["groq"]["successes"] == 1
