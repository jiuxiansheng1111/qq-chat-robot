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


async def test_long_term_memory_is_persistent_pruned_and_clearable(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "memory.db")))
    await db.init()
    for number in range(4):
        await db.add_long_term_memory("100", "200", f"memory-{number}", max_items=3)
    assert await db.long_term_memories("100", "200") == [
        "memory-1",
        "memory-2",
        "memory-3",
    ]
    await db.clear_long_term_memories("100", "200")
    assert await db.long_term_memories("100", "200") == []


async def test_activity_possession_and_anonymous_style_hint(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "activity.db")))
    await db.init()
    for _ in range(15):
        await db.record_group_activity("100", "200", "小明", "哈哈，确实！", "2026-09-18")

    possession = await db.get_or_create_daily_possession("100", "2026-09-18")
    assert possession == ("200", "小明", "random")
    reopened = Database(Settings(_env_file=None, database_path=str(tmp_path / "activity.db")))
    await reopened.init()
    assert await reopened.daily_possession("100", "2026-09-18") == possession
    assert await db.get_or_create_daily_possession("100", "2026-09-18") == possession
    await db.exit_daily_possession("100", "2026-09-18", "200")
    assert await db.possession_exited("100", "2026-09-18")
    assert await db.daily_possession("100", "2026-09-18") is None
    assert await db.get_or_create_daily_possession("100", "2026-09-18") is None
    hint = await db.group_style_hint("100")
    assert "短句为主" in hint
    assert "哈哈" in hint
    assert "小明" not in hint


async def test_possession_requires_more_than_five_messages(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "threshold.db")))
    await db.init()
    for _ in range(14):
        await db.record_group_activity("100", "200", "小明", "测试", "2026-09-18")
    assert await db.get_or_create_daily_possession("100", "2026-09-18") is None


async def test_old_random_result_is_rechecked_against_new_threshold(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "old-random.db")))
    await db.init()
    for _ in range(6):
        await db.record_group_activity("100", "200", "小明", "测试", "2026-09-18")
    await db.execute(
        "INSERT INTO daily_possession(group_id, possession_date, user_id, display_name, mode) "
        "VALUES (?, ?, ?, ?, 'random')",
        ("100", "2026-09-18", "200", "小明"),
    )
    assert await db.get_or_create_daily_possession("100", "2026-09-18") is None


async def test_targeted_possession_does_not_require_activity_threshold(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "targeted.db")))
    await db.init()
    assert await db.set_targeted_possession("100", "2026-09-18", "300", "小红")
    assert await db.daily_possession("100", "2026-09-18") == ("300", "小红", "targeted")
