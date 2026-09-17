from app.plugins.registry import PluginSpec, registry


async def hello(context) -> str:
    return "你好，我是 qqchat robot。"


registry.register(
    PluginSpec(
        name="hello",
        commands=("/hello", "/你好"),
        description="/hello —— 测试自定义插件",
        handler=hello,
    )
)
