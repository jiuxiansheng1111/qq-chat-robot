class Hooks:
    async def start_part(self, host):
        import asyncio
        service = host._get_music_listening()
        self.recovery = asyncio.create_task(service.restore(host.runtime._core.settings, host.context,
                                                            route_allowed=host._legacy_event_allowed))

    async def stop_part(self, host):
        import asyncio
        self.recovery.cancel()
        await asyncio.gather(self.recovery, return_exceptions=True)
        await host._music_listening.close(preserve=True)
        host._music_listening = None

    async def message(self, host, event):
        host._get_music_listening().observe(event)

    async def prepare(self, host, event, request, enabled):
        from app.astrbot_qq_persona import is_qq
        if is_qq(event):
            host._get_music_listening().prepare(event, request)
