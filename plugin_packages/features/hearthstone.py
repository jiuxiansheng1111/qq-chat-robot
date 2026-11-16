class Hooks:
    prepare_order = 40

    @filter.command("炉石", alias={"炉石卡组", "炉石分析", "炉石替换", "卡组分析", "替换卡"})
    async def decks(self, event: AstrMessageEvent):
        """按模式查询、分析卡组或选择替换卡。"""
        host = self.host()
        if host is not None:
            from app.astrbot_hearthstone import wake_command
            await wake_command(host, event)

    async def prepare(self, host, event, request, enabled):
        from app.astrbot_hearthstone import prepare
        await prepare(host, event, request, arena=False, cards=False)
