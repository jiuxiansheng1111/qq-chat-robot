import asyncio
import time
from dataclasses import dataclass


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_seconds: float = 30):
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = asyncio.Lock()

    async def allow(self) -> bool:
        async with self._lock:
            if self.opened_at is None:
                return True
            if time.monotonic() - self.opened_at >= self.recovery_seconds:
                self.opened_at = None
                self.failures = 0
                return True
            return False

    async def success(self) -> None:
        async with self._lock:
            self.failures = 0
            self.opened_at = None

    async def failure(self) -> None:
        async with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.opened_at = time.monotonic()


@dataclass
class ProviderStats:
    requests: int = 0
    successes: int = 0
    failures: int = 0
    rate_limited: int = 0
    total_latency_ms: float = 0

    def record(self, *, success: bool, latency_ms: float, rate_limited: bool = False) -> None:
        self.requests += 1
        self.total_latency_ms += latency_ms
        if success:
            self.successes += 1
        else:
            self.failures += 1
        if rate_limited:
            self.rate_limited += 1

    def snapshot(self) -> dict:
        average = self.total_latency_ms / self.requests if self.requests else 0
        return {
            "requests": self.requests,
            "successes": self.successes,
            "failures": self.failures,
            "rate_limited": self.rate_limited,
            "average_latency_ms": round(average, 2),
        }
