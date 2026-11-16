class Hooks:
    async def retry_lookup(self, host, event, mode, name):
        from .logic import handle
        return await handle(host, event, command=True, corrected=(mode, name))

    async def message(self, host, event):
        from .logic import handle
        return await handle(host, event)

    @filter.command("查卡", alias={"炉石查卡", "查询卡牌", "查询卡片", "卡牌查询", "卡片查询", "查卡确认"})
    async def lookup(self, event: AstrMessageEvent):
        """搜索卡名或简称，在玩家确认后发卡面和详情。"""
        host = self.host()
        if host is not None:
            from .logic import handle
            await handle(host, event, command=True)
