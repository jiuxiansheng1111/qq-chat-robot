class Hooks:
    async def message(self, host, event):
        from app.astrbot_mahjong import deliver_native_review, request_args
        args = request_args(event, host.runtime._core.app.state)
        if args is None:
            return False
        event.should_call_llm(False)
        event.stop_event()
        await deliver_native_review(host.runtime, event, args)
        return True
