"""An owned loopback listener that shares AstrBot's event loop."""

import asyncio
import socket
from contextlib import contextmanager, suppress

import uvicorn


class _Server(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        yield


class LocalListener:
    def __init__(self, port):
        self.port = port
        self.server = self.socket = self.task = None

    async def start(self, app):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", self.port))
            listener.listen(16)
            listener.setblocking(False)
            self.socket = listener
            self.server = _Server(uvicorn.Config(app, log_level="warning", access_log=False))
            self.task = asyncio.create_task(self.server.serve(sockets=[listener]))
            async with asyncio.timeout(5):
                while not self.server.started:
                    if self.task.done():
                        await self.task
                        raise OSError("本机服务未完成启动")
                    await asyncio.sleep(0.02)
        except BaseException:
            listener.close()
            await self.close()
            raise

    async def close(self):
        if self.server:
            self.server.should_exit = True
        if self.task:
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 5)
            except TimeoutError:
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
        if self.socket:
            self.socket.close()
        self.server = self.socket = self.task = None
