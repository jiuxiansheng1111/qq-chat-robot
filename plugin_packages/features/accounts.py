class Hooks:
    async def start_part(self, host):
        from app.services.mahjong.account_panel import AccountPanel
        self.panel = AccountPanel(host.root, host._get_companion_turns().games.mc, host.context)
        await self.panel.start()

    async def stop_part(self, host):
        await self.panel.close()
