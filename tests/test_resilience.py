from app.llm.resilience import CircuitBreaker, ProviderStats


async def test_circuit_breaker_opens_after_failures():
    breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=60)
    assert await breaker.allow()
    await breaker.failure()
    await breaker.failure()
    assert not await breaker.allow()
    await breaker.success()
    assert await breaker.allow()


def test_provider_stats_snapshot():
    stats = ProviderStats()
    stats.record(success=True, latency_ms=10)
    stats.record(success=False, latency_ms=30, rate_limited=True)
    assert stats.snapshot() == {
        "requests": 2,
        "successes": 1,
        "failures": 1,
        "rate_limited": 1,
        "average_latency_ms": 20.0,
    }
