class Hooks:
    @filter.command("肥鱼插件", alias={"肥鱼菜单", "大肥鱼菜单"})
    async def show_modules(self, event: AstrMessageEvent):
        """列出本会话实际启用的大肥鱼功能。"""
        host = self.host()
        if host is None:
            return
        from app.astrbot_menu import _raw_event_for_reset
        if not await host._legacy_event_allowed(_raw_event_for_reset(host, event)):
            return
        text = "\n".join(part.label + "  " + part.summary for part in sorted(host.parts(event), key=lambda p: p.label))
        await event.send(event.plain_result("本会话已启用的功能\n" + text))
        event.should_call_llm(False)
        event.stop_event()

    async def message(self, host, event):
        from app.astrbot_menu import menu_request_kind
        text = event.get_message_str().strip().lstrip("/")
        if text in {"肥鱼菜单", "大肥鱼菜单", "肥鱼插件"} or menu_request_kind(text):
            await self.show_modules(event)
            return True
