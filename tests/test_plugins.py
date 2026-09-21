from types import SimpleNamespace

from app.plugins.registry import registry


def test_plugin_registry_help():
    assert registry.find("/ai").name == "ai"
    text = registry.help_text()
    assert "@我 + 想说的话" in text
    assert "@我 随机猫咪" in text
    assert "@我 随机猪猪" in text
    assert "@我 随机奶龙" in text
    assert "@我 点歌" in text
    assert "@我 今日奥特曼" in text
    assert "@我 我的奥特曼" in text
    assert "@我 奥特曼图鉴" in text
    assert "@我 贝利亚" in text
    assert "/hello" not in text


async def test_loaded_plugin_can_handle_message():
    registry.load_modules("app.plugins.hello")
    result = await registry.dispatch("/hello", SimpleNamespace(is_admin=False))
    assert result == ("hello", "你好，我是 qqchat robot。")
