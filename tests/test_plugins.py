from types import SimpleNamespace

from app.plugins.registry import registry


def test_plugin_registry_help():
    assert registry.find("/ai").name == "ai"
    text = registry.help_text()
    assert "/猫" in text
    assert "/小猪" in text


async def test_loaded_plugin_can_handle_message():
    registry.load_modules("app.plugins.hello")
    result = await registry.dispatch("/hello", SimpleNamespace(is_admin=False))
    assert result == ("hello", "你好，我是 qqchat robot。")
