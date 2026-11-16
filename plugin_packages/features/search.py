class Hooks:
    prepare_order = 70

    async def prepare(self, host, event, request, enabled):
        from app.astrbot_search import prepare_group_search_context
        from .logic import prepare_guide_context
        settings = host.runtime._core.settings
        await prepare_group_search_context(host.context, event, request, settings=settings,
                                           enabled=host.config.get("web_search_enabled", True),
                                           media_features=enabled)
        await prepare_guide_context(host.context, event, request, settings)
