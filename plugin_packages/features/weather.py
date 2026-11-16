class Hooks:
    async def start_part(self, host):
        from app.astrbot_weather_alerts import service_for
        service_for(host).start(host)

    async def stop_part(self, host):
        await host._weather_alerts.close()
        host._weather_alerts = None

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
        from .logic import deliver
        await deliver(host, event, city)
