class Hooks:
    async def prepare(self, host, event, request, enabled):
        from app.astrbot_soundtry import prepare_soundtry
        prepare_soundtry(event, request, host._soundtry_jobs)
