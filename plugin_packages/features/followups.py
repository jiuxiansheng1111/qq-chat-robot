class Hooks:
    async def start_part(self, host):
        host._get_companion_turns().start_followups(host)

    async def stop_part(self, host):
        await host._get_companion_turns().stop_followups()
