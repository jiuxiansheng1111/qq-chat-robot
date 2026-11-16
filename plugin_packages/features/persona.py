class Hooks:
    prepare_order = 90

    async def prepare(self, host, event, request, enabled):
        from app.astrbot_qq_persona import apply_qq_persona
        apply_qq_persona(host.context, event, request)
        host._get_group_router().prepare_request(event, request)

    async def message(self, host, event):
        from app.astrbot_failure_reply import install_send_guard
        install_send_guard(host.context, event)

    @filter.after_message_sent(priority=-300000)
    async def finish_group_reply(self, event: AstrMessageEvent):
        host = self.host()
        if host is None:
            return
        if host._group_router is not None:
            host._group_router.finish_event(event, sent=bool(getattr(event, "_has_send_oper", False)))
        if host._reply_guard is not None:
            host._reply_guard.mark_sent(event)
