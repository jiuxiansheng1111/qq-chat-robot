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
    assert "@我 图鉴" in text
    assert "@我 今日奥特曼" in text
    assert "@我 本命奥特曼" in text
    assert "@我 随机二次元角色" in text
    assert "@我 本命二次元角色" in text
    assert "@我 天气 新加坡" in text
    assert "@我 开启语音" in text
    assert "@我 音色列表" in text
    assert "@我 丛雨语音" in text
    assert "@我 生成图片" in text
    assert registry.find("图鉴").name == "character_catalog"
    assert registry.find("奥特曼图鉴").name == "ultraman"
    assert registry.find("二次元角色图鉴").name == "anime_character"
    assert "/hello" not in text


async def test_loaded_plugin_can_handle_message():
    registry.load_modules("app.plugins.hello")
    result = await registry.dispatch("/hello", SimpleNamespace(is_admin=False))
    assert result == ("hello", "你好，我是 qqchat robot。")
