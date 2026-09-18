import asyncio
import time

import httpx

from app.config import Settings
from app.llm.providers import LLMError, OpenAICompatibleProvider
from app.llm.resilience import CircuitBreaker, CircuitOpenError, ProviderStats


class LLMManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
        self.capacity = asyncio.BoundedSemaphore(settings.llm_max_concurrency + settings.llm_queue_size)
        self.provider = self._provider(settings.llm_provider)
        self.fallback = self._provider(settings.llm_fallback_provider)
        self.provider_name = settings.llm_provider
        self.fallback_name = settings.llm_fallback_provider
        self.breakers = {
            self.provider_name: CircuitBreaker(),
            self.fallback_name: CircuitBreaker(),
        }
        self.stats = {
            self.provider_name: ProviderStats(),
            self.fallback_name: ProviderStats(),
        }

    def _provider(self, name: str):
        if name == "zhipu":
            return OpenAICompatibleProvider("zhipu", self.settings.llm_base_url, self.settings.llm_api_key, self.settings.llm_model, self.settings.llm_timeout_seconds, self.settings.llm_max_output_tokens, self.settings.llm_temperature)
        if name == "groq":
            return OpenAICompatibleProvider("groq", self.settings.groq_base_url, self.settings.groq_api_key, self.settings.groq_model, self.settings.llm_timeout_seconds, self.settings.llm_max_output_tokens, self.settings.llm_temperature)
        raise ValueError(f"不支持的 LLM provider: {name}")

    async def ask(self, messages: list[dict]) -> str:
        try:
            await asyncio.wait_for(self.capacity.acquire(), timeout=self.settings.llm_queue_timeout_seconds)
        except TimeoutError as exc:
            raise LLMError("llm_queue_full") from exc
        running = False
        try:
            try:
                await asyncio.wait_for(self.semaphore.acquire(), timeout=self.settings.llm_queue_timeout_seconds)
                running = True
            except TimeoutError as exc:
                raise LLMError("llm_queue_timeout") from exc
            try:
                return await self._call(self.provider_name, self.provider, messages)
            except (LLMError, httpx.HTTPError):
                if self.fallback is self.provider:
                    raise
                return await self._call(self.fallback_name, self.fallback, messages)
        finally:
            if running:
                self.semaphore.release()
            self.capacity.release()

    async def _call(self, name: str, provider, messages: list[dict]) -> str:
        breaker = self.breakers[name]
        if not await breaker.allow():
            raise LLMError(f"{name} circuit open") from CircuitOpenError(name)
        started = time.perf_counter()
        try:
            result = await provider.chat(messages)
        except (LLMError, httpx.HTTPError) as exc:
            await breaker.failure()
            self.stats[name].record(
                success=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                rate_limited="429" in str(exc),
            )
            raise
        else:
            await breaker.success()
            self.stats[name].record(success=True, latency_ms=(time.perf_counter() - started) * 1000)
            return result

    def snapshot(self) -> dict[str, dict]:
        return {name: stats.snapshot() for name, stats in self.stats.items()}

    async def aclose(self) -> None:
        providers = {id(self.provider): self.provider, id(self.fallback): self.fallback}
        for provider in providers.values():
            close = getattr(provider, "aclose", None)
            if close:
                await close()
