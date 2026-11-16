class Hooks:
    async def start_part(self, host):
        from app.minecraft_bridge import MinecraftBridge
        runtime = host.runtime._core
        self.bridge = MinecraftBridge(runtime.app.state.llm, host.root, settings=runtime.settings)
        await self.bridge.start()

    async def stop_part(self, host):
        if host._companion_turns:
            await host._companion_turns.games.mc.close()
        await self.bridge.close()
