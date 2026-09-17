from app.config import Settings
from app.db.database import Database


async def test_group_switch_and_blacklist(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "groups.db")))
    await db.init()
    assert await db.group_enabled("100")
    await db.set_group_enabled("100", False)
    assert not await db.group_enabled("100")
    await db.set_blocked("100", "200", True)
    assert await db.is_blocked("100", "200")
    await db.set_blocked("100", "200", False)
    assert not await db.is_blocked("100", "200")


async def test_plugin_settings_default_enabled_and_configurable(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "plugins.db")))
    await db.init()
    assert await db.plugin_enabled("100", "weather")
    await db.set_plugin_enabled("100", "weather", False)
    assert not await db.plugin_enabled("100", "weather")
