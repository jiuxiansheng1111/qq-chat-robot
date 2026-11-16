class Hooks:
    async def start_part(self, host):
        from app.astrbot_turn_features import start_timers
        start_timers(host, ("study",))

    @filter.on_decorating_result(priority=110)
    async def quiet_while_studying(self, event: AstrMessageEvent):
        host = self.host()
        if host is not None:
            from app.astrbot_turn_features import quiet_study_event
            if await quiet_study_event(host, event):
                event.clear_result()
                event.stop_event()
