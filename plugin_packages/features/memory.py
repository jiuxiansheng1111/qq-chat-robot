class Hooks:
    async def message(self, host, event):
        from app.astrbot_owner_memory import observe
        await observe(event)

    async def prepare(self, host, event, request, enabled):
        from app.astrbot_owner_memory import prepare
        await prepare(event, request, host.runtime._core.app.state.db)

    @filter.after_message_sent(priority=-300001)
    async def archive_reply(self, event: AstrMessageEvent):
        if self.host() is not None:
            from app.astrbot_owner_memory import sent
            await sent(event)
