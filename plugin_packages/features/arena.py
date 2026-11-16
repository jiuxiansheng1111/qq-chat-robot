class Hooks:
    prepare_order = 35

    async def prepare(self, host, event, request, enabled):
        from app.astrbot_hearthstone import prepare
        await prepare(host, event, request, decks=False, cards=False)
