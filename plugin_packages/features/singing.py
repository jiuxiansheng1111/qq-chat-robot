class Hooks:
    async def message(self, host, event):
        from app.astrbot_singing import handle_command
        return await handle_command(host, event)

    async def prepare(self, host, event, request, enabled):
        from app.astrbot_singing import prepare_audio_capabilities
        await prepare_audio_capabilities(host.runtime._core, event, request)
