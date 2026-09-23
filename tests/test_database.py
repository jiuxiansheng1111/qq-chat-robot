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
    assert not await db.possession_exited("100", "2026-09-18")
    assert await db.daily_possession("100", "2026-09-18") is None
    assert await db.get_or_create_daily_possession("100", "2026-09-18") == possession
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
    await db.rename_daily_possession("100", "2026-09-18", "red")
    assert await db.daily_possession("100", "2026-09-18") == ("300", "red", "targeted")
    await db.exit_daily_possession("100", "2026-09-18", "300")
    assert await db.set_targeted_possession("100", "2026-09-18", "400", "小蓝")
    assert await db.daily_possession("100", "2026-09-18") == ("400", "小蓝", "targeted")


async def test_random_possession_can_reroll_without_daily_limit(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "reroll.db")))
    await db.init()
    for user_id, name in (("200", "小明"), ("300", "小红")):
        for _ in range(15):
            await db.record_group_activity(
                "100", user_id, name, "测试", "2026-09-18"
            )
    first = await db.get_or_create_daily_possession("100", "2026-09-18")
    second = await db.get_or_create_daily_possession(
        "100", "2026-09-18", reroll=True
    )
    assert first is not None and second is not None
    assert second[0] != first[0]


async def test_possession_style_profile_is_persistent(tmp_path):
    path = tmp_path / "possession-style.db"
    db = Database(Settings(_env_file=None, database_path=str(path)))
    await db.init()
    assert await db.possession_style_profile("100", "300") == ""
    await db.save_possession_style_profile(
        "100", "300", "小红", "短句为主，偶尔使用颜文字。", 12
    )
    reopened = Database(Settings(_env_file=None, database_path=str(path)))
    await reopened.init()
    assert await reopened.possession_style_profile("100", "300") == (
        "短句为主，偶尔使用颜文字。"
    )


async def test_possession_style_messages_persist_and_can_be_deleted(tmp_path):
    path = tmp_path / "possession-style-messages.db"
    db = Database(Settings(_env_file=None, database_path=str(path)))
    await db.init()
    await db.add_possession_style_message("100", "300", "小红", "我最喜欢干山乃乃")
    await db.save_possession_style_profile("100", "300", "小红", "短句", 1)
    assert await db.possession_style_messages("100", "300") == ["我最喜欢干山乃乃"]
    await db.clear_possession_style("100", "300")
    assert await db.possession_style_messages("100", "300") == []
    assert await db.possession_style_profile("100", "300") == ""


async def test_possession_recall_messages_can_read_deeper_history(tmp_path):
    path = tmp_path / "possession-recall.db"
    db = Database(Settings(_env_file=None, database_path=str(path)))
    await db.init()
    for number in range(140):
        await db.add_possession_style_message(
            "100",
            "300",
            "羽入",
            f"message-{number}",
            max_messages=120,
        )

    recalled = await db.possession_recall_messages("100", "300", limit=500)
    assert len(recalled) == 120
    assert recalled[0] == "message-20"
    assert recalled[-1] == "message-139"
    assert await db.possession_style_messages("100", "300", limit=8) == [
        f"message-{number}" for number in range(132, 140)
    ]


async def test_possession_context_is_shared_persistent_and_bounded(tmp_path):
    path = tmp_path / "possession-context.db"
    db = Database(Settings(_env_file=None, database_path=str(path)))
    await db.init()
    for number in range(3):
        await db.append_possession_exchange(
            "100", "2026-09-18", f"question-{number}", f"answer-{number}", max_messages=4
        )
    reopened = Database(Settings(_env_file=None, database_path=str(path)))
    await reopened.init()
    assert await reopened.possession_context_messages("100", "2026-09-18") == [
        {"role": "user", "content": "question-1"},
        {"role": "assistant", "content": "answer-1"},
        {"role": "user", "content": "question-2"},
        {"role": "assistant", "content": "answer-2"},
    ]


async def test_group_memory_persists_across_possession_targets(tmp_path):
    path = tmp_path / "group-memory.db"
    db = Database(Settings(_env_file=None, database_path=str(path)))
    await db.init()
    await db.add_group_memory("100", "hzh 是 Cat#")
    await db.set_targeted_possession("100", "2026-09-18", "200", "Cat#")
    await db.set_targeted_possession("100", "2026-09-18", "300", "首阳")
    reopened = Database(Settings(_env_file=None, database_path=str(path)))
    await reopened.init()
    assert await reopened.group_memories("100") == ["hzh 是 Cat#"]


async def test_group_memory_can_be_deleted_by_exact_substring(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "delete-memory.db")))
    await db.init()
    await db.add_group_memory("100", "hzh 是 Cat#")
    await db.add_group_memory("100", "hzh 性格很活泼")
    await db.add_group_memory("100", "首阳喜欢唱歌")
    await db.append_possession_exchange("100", "2026-09-18", "hzh 是谁", "Cat#")
    assert await db.delete_group_memories_matching("100", "hzh") == 2
    assert await db.group_memories("100") == ["首阳喜欢唱歌"]
    assert await db.possession_context_messages("100", "2026-09-18") == []
    assert await db.delete_group_memories_matching("100", "HZH") == 0


async def test_daily_ultraman_collection_is_persistent_and_counted_once_per_day(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "ultraman.db")))
    await db.init()
    first = await db.get_or_create_daily_ultraman(
        "100", "200", "2026-09-19", "迪迦奥特曼"
    )
    repeated = await db.get_or_create_daily_ultraman(
        "999", "200", "2026-09-19", "赛罗奥特曼"
    )
    await db.get_or_create_daily_ultraman(
        "100", "200", "2026-09-20", "迪迦奥特曼"
    )
    await db.get_or_create_daily_ultraman(
        "100", "200", "2026-09-21", "泽塔奥特曼"
    )
    assert first == ("迪迦奥特曼", True)
    assert repeated == ("迪迦奥特曼", False)
    assert await db.ultraman_collection_stats("200") == (
        3,
        2,
        "迪迦奥特曼",
        2,
    )


async def test_database_migrates_audited_ultraman_form_names(tmp_path):
    settings = Settings(database_path=str(tmp_path / "legacy-forms.db"), _env_file=None)
    db = Database(settings)
    await db.init()
    migrations = {
        "欧布奥特曼·雷霆肩章": "欧布奥特曼·暗耀形态",
        "梦比优斯无限形态": "梦比优斯奥特曼·无限形态",
        "强壮日冕赛罗": "赛罗奥特曼·强壮日冕型",
        "月神奇迹赛罗": "赛罗奥特曼·月神奇迹型",
        "闪耀赛罗": "赛罗奥特曼·闪耀型",
        "赛罗奥特曼·超越形态": "赛罗奥特曼·无限形态",
        "银河斯特利姆": "银河奥特曼·斯特利姆形态",
        "银河维克特利": "银河维克特利奥特曼",
        "艾克斯奥特曼·超越型": "艾克斯奥特曼·超越形态",
        "欧布奥特曼·斯佩修姆哉佩利敖": "欧布奥特曼·重光形态",
        "欧布奥特曼·燃烧炸弹": "欧布奥特曼·暴炎形态",
        "欧布奥特曼·闪电攻击者": "欧布奥特曼·煌闪形态",
        "欧布奥特曼·艾梅利姆头镖": "欧布奥特曼·智勇形态",
        "泰迦奥特曼·三重斯特利姆": "泰迦奥特曼·三重斯特利姆形态",
        "特利迦真理形态": "真理特利迦",
        "布莱泽奥特曼·法德兰装甲": "布莱泽奥特曼·法多兰盔甲",
        "亚刻奥特曼·太阳装甲": "亚刻奥特曼·索利斯装甲",
        "亚刻奥特曼·月亮装甲": "亚刻奥特曼·露娜装甲",
        "欧米伽奥特曼·雷基尼斯装甲": "欧米伽奥特曼·雷金斯装甲",
        "欧米伽奥特曼·瓦尔格尼斯装甲": "欧米伽奥特曼·瓦尔根斯装甲",
        "欧米伽奥特曼·盖梅顿装甲": "欧米伽奥特曼·加梅顿装甲",
    }

    for index, old_name in enumerate(migrations):
        await db.execute(
            "INSERT INTO daily_ultraman(user_id, draw_date, group_id, ultraman_name) "
            "VALUES (?, ?, ?, ?)",
            (f"user-{index}", "2026-09-20", "group", old_name),
        )

    await db.init()

    rows = await db.fetchall(
        "SELECT user_id, ultraman_name FROM daily_ultraman ORDER BY user_id"
    )
    migrated = {name for _, name in rows}
    assert migrated == set(migrations.values())




async def test_group_member_identity_memory_is_private_keyed_and_clearable(tmp_path):
    db = Database(Settings(_env_file=None, database_path=str(tmp_path / "member-id.db")))
    await db.init()
    await db.add_group_member_identity("100", "184573813", "狄", "drj")
    await db.add_group_member_identity("100", "184573813", "狄", "猫猫")

    assert await db.group_member_identities("100", "184573813") == ["猫猫", "drj"]

    by_qq = await db.find_group_member_identity("100", "184573813")
    assert by_qq[0] == ("184573813", "狄", "猫猫")

    by_alias = await db.find_group_member_identity("100", "drj")
    assert by_alias == [("184573813", "狄", "drj")]

    assert await db.latest_group_member_display_name("100", "184573813") == "狄"

    await db.clear_group_member_identities("100", "184573813")
    assert await db.group_member_identities("100", "184573813") == []
