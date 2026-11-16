class Hooks:
    prepare_order = 25

    async def start_part(self, host):
        from app.astrbot_vision_provider import register_vision_provider
        register_vision_provider()

    async def prepare(self, host, event, request, enabled):
        from app.astrbot_vision import prepare_group_image_context
        from app.astrbot_search import _text_only_chat_provider
        prepare_group_image_context(event, request, text_only=await _text_only_chat_provider(host.context, event))
