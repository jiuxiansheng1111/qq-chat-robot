class Hooks:
    async def start_part(self, host):
        from app.astrbot_turn_features import start_timers
        start_timers(host, ("time_capsule",))
