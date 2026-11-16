class Hooks:
    async def message(self, host, event):
        from app.astrbot_chat import handle_voice_exit
        if await handle_voice_exit(host.runtime, event):
            if host._companion_turns:
                host._companion_turns.voice_off(event)
            return True

    @filter.on_decorating_result(priority=0)
    async def voice_reply(self, event: AstrMessageEvent):
        host = self.host()
        if host is not None:
            from app.astrbot_chat import decorate_native_voice
            await decorate_native_voice(host.runtime, event)
