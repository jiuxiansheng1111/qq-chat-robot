class Hooks:
    async def message(self, host, event):
        from .logic import handle_command
        return await handle_command(host, event)

    async def prepare(self, host, event, request, enabled):
        from .logic import prepare
        prepare(event, request)

    @filter.command("天气", alias={"weather"})
    async def weather_command(self, event: AstrMessageEvent, city: str = ""):
        """查询指定城市的天气。"""
        host = self.host()
        if host is None:
            return
        from .logic import query
        result = await query(host, event, city)
        if result["status"] == "denied":
            return
        text = result.get("report") or ("想查哪个城市的天气？" if not city else "暂时没有查到这座城市的天气。")
        await event.send(event.plain_result(text))
        event.should_call_llm(False)
        event.stop_event()
