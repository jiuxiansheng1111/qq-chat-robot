"""Restartable delivery loop for opted-in QQ activities; durable claims live in each activity."""
import asyncio
import logging
from datetime import datetime

from app.services.social_plans import CHINA, PlanStore


class ActivityTimer:
    def __init__(self, clock=lambda: datetime.now(CHINA)):
        self.clock = clock
        self.task = None
        self.wake = asyncio.Event()

    def store(self, host):
        return PlanStore(host.runtime.project_root / "data/dafeiyu" / (self.slug + ".sqlite3"), self.schema)

    def start(self, host):
        if self.store(host).path.exists() and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.run(host), name="qq-" + self.slug)
        self.wake.set()

    async def run(self, host):
        while True:
            self.wake.clear()
            try:
                await self.check_due(host)
            except Exception as exc:  # noqa: BLE001 -- adapter errors must not terminate future scheduled sessions
                logging.getLogger(__name__).warning("QQ activity timer %s: %s", self.slug, type(exc).__name__)
            try:
                await asyncio.wait_for(self.wake.wait(), 15)
            except TimeoutError:
                pass

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
