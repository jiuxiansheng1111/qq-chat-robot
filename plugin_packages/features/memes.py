class Hooks:
    async def prepare(self, host, event, request, enabled):
        host._get_qq_memes().prepare(host.context, event, request)

    @filter.after_message_sent(priority=-300000)
    async def finish_meme(self, event: AstrMessageEvent):
        host = self.host()
        if host is not None and host._qq_memes is not None:
            host._qq_memes.mark_sent(event)
