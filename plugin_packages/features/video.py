class Hooks:
    prepare_order = 30

    async def prepare(self, host, event, request, enabled):
        from app.astrbot_video import prepare_group_video_context
        await prepare_group_video_context(host.context, event, request, settings=host.runtime._core.settings)
