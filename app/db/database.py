import secrets
from pathlib import Path

import aiosqlite

from app.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.path = settings.database_path

    async def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'admin',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    jti TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    replaced_by TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS group_settings (
                    group_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS group_blacklist (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS user_preferences (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    memory_enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS group_plugins (
                    group_id TEXT NOT NULL,
                    plugin_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, plugin_name)
                );
                CREATE TABLE IF NOT EXISTS long_term_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_long_term_memories_owner
                    ON long_term_memories(group_id, user_id, id);
                CREATE TABLE IF NOT EXISTS daily_activity (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    activity_date TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    display_name TEXT NOT NULL,
                    PRIMARY KEY (group_id, user_id, activity_date)
                );
                CREATE TABLE IF NOT EXISTS daily_possession (
                    group_id TEXT NOT NULL,
                    possession_date TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'random',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, possession_date)
                );
                CREATE TABLE IF NOT EXISTS daily_possession_exits (
                    group_id TEXT NOT NULL,
                    possession_date TEXT NOT NULL,
                    exited_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, possession_date)
                );
                CREATE TABLE IF NOT EXISTS possession_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    possession_date TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_possession_context_group_date
                    ON possession_context(group_id, possession_date, id);
                CREATE TABLE IF NOT EXISTS possession_style_profiles (
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    style_summary TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (group_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS possession_style_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    has_image INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_possession_style_messages_member
                    ON possession_style_messages(group_id, user_id, id);
                CREATE TABLE IF NOT EXISTS group_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_group_memories_group
                    ON group_memories(group_id, id);
                CREATE TABLE IF NOT EXISTS group_member_identities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(group_id, user_id, alias)
                );
                CREATE INDEX IF NOT EXISTS idx_group_member_identities_owner
                    ON group_member_identities(group_id, user_id, id);
                CREATE TABLE IF NOT EXISTS group_style_stats (
                    group_id TEXT PRIMARY KEY,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    total_chars INTEGER NOT NULL DEFAULT 0,
                    question_count INTEGER NOT NULL DEFAULT 0,
                    exclamation_count INTEGER NOT NULL DEFAULT 0,
                    kaomoji_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS group_style_terms (
                    group_id TEXT NOT NULL,
                    term TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (group_id, term)
                );
                CREATE TABLE IF NOT EXISTS daily_ultraman (
                    user_id TEXT NOT NULL,
                    draw_date TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    ultraman_name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, draw_date)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_ultraman_user
                    ON daily_ultraman(user_id, draw_date);
                CREATE TABLE IF NOT EXISTS daily_anime_character (
                    user_id TEXT NOT NULL,
                    draw_date TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    character_name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, draw_date)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_anime_character_user
                    ON daily_anime_character(user_id, draw_date);
                """
            )
            columns = await db.execute_fetchall("PRAGMA table_info(daily_possession)")
            if "mode" not in {column[1] for column in columns}:
                await db.execute(
                    "ALTER TABLE daily_possession ADD COLUMN mode TEXT NOT NULL DEFAULT 'random'"
                )
            # Keep old collection rows valid when display names are corrected.
            ultraman_name_migrations = (
                ("欧布奥特曼·雷霆肩章", "欧布奥特曼·暗耀形态"),
                ("梦比优斯无限形态", "梦比优斯奥特曼·无限形态"),
                ("强壮日冕赛罗", "赛罗奥特曼·强壮日冕型"),
                ("月神奇迹赛罗", "赛罗奥特曼·月神奇迹型"),
                ("闪耀赛罗", "赛罗奥特曼·闪耀型"),
                ("赛罗奥特曼·超越形态", "赛罗奥特曼·无限形态"),
                ("银河斯特利姆", "银河奥特曼·斯特利姆形态"),
                ("银河维克特利", "银河维克特利奥特曼"),
                ("艾克斯奥特曼·超越型", "艾克斯奥特曼·超越形态"),
                ("欧布奥特曼·斯佩修姆哉佩利敖", "欧布奥特曼·重光形态"),
                ("欧布奥特曼·燃烧炸弹", "欧布奥特曼·暴炎形态"),
                ("欧布奥特曼·闪电攻击者", "欧布奥特曼·煌闪形态"),
                ("欧布奥特曼·艾梅利姆头镖", "欧布奥特曼·智勇形态"),
                ("泰迦奥特曼·三重斯特利姆", "泰迦奥特曼·三重斯特利姆形态"),
                ("特利迦真理形态", "真理特利迦"),
                ("布莱泽奥特曼·法德兰装甲", "布莱泽奥特曼·法多兰盔甲"),
                ("亚刻奥特曼·太阳装甲", "亚刻奥特曼·索利斯装甲"),
                ("亚刻奥特曼·月亮装甲", "亚刻奥特曼·露娜装甲"),
                ("欧米伽奥特曼·雷基尼斯装甲", "欧米伽奥特曼·雷金斯装甲"),
                ("欧米伽奥特曼·瓦尔格尼斯装甲", "欧米伽奥特曼·瓦尔根斯装甲"),
                ("欧米伽奥特曼·盖梅顿装甲", "欧米伽奥特曼·加梅顿装甲"),
            )
            await db.executemany(
                "UPDATE daily_ultraman SET ultraman_name = ? WHERE ultraman_name = ?",
                ((new_name, old_name) for old_name, new_name in ultraman_name_migrations),
            )
            await db.commit()

    async def execute(self, sql: str, params: tuple = ()) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(sql, params)
            await db.commit()

    async def fetchone(self, sql: str, params: tuple = ()):
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(sql, params)
            return await cursor.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()):
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(sql, params)
            return await cursor.fetchall()

    async def group_enabled(self, group_id: str) -> bool:
        row = await self.fetchone("SELECT enabled FROM group_settings WHERE group_id = ?", (group_id,))
        return bool(row[0]) if row else True

    async def set_group_enabled(self, group_id: str, enabled: bool) -> None:
        await self.execute(
            "INSERT INTO group_settings(group_id, enabled) VALUES (?, ?) "
            "ON CONFLICT(group_id) DO UPDATE SET enabled = excluded.enabled, updated_at = CURRENT_TIMESTAMP",
            (group_id, int(enabled)),
        )

    async def is_blocked(self, group_id: str, user_id: str) -> bool:
        row = await self.fetchone(
            "SELECT 1 FROM group_blacklist WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        return row is not None

    async def set_blocked(self, group_id: str, user_id: str, blocked: bool) -> None:
        if blocked:
            await self.execute(
                "INSERT OR IGNORE INTO group_blacklist(group_id, user_id) VALUES (?, ?)",
                (group_id, user_id),
            )
        else:
            await self.execute(
                "DELETE FROM group_blacklist WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            )

    async def memory_enabled(self, group_id: str, user_id: str) -> bool:
        row = await self.fetchone(
            "SELECT memory_enabled FROM user_preferences WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        return bool(row[0]) if row else False

    async def set_memory_enabled(self, group_id: str, user_id: str, enabled: bool) -> None:
        await self.execute(
            "INSERT INTO user_preferences(group_id, user_id, memory_enabled) VALUES (?, ?, ?) "
            "ON CONFLICT(group_id, user_id) DO UPDATE SET memory_enabled = excluded.memory_enabled, updated_at = CURRENT_TIMESTAMP",
            (group_id, user_id, int(enabled)),
        )

    async def plugin_enabled(self, group_id: str, plugin_name: str) -> bool:
        row = await self.fetchone(
            "SELECT enabled FROM group_plugins WHERE group_id = ? AND plugin_name = ?",
            (group_id, plugin_name),
        )
        return bool(row[0]) if row else True

    async def set_plugin_enabled(self, group_id: str, plugin_name: str, enabled: bool) -> None:
        await self.execute(
            "INSERT INTO group_plugins(group_id, plugin_name, enabled) VALUES (?, ?, ?) "
            "ON CONFLICT(group_id, plugin_name) DO UPDATE SET enabled = excluded.enabled, updated_at = CURRENT_TIMESTAMP",
            (group_id, plugin_name, int(enabled)),
        )

    async def add_long_term_memory(
        self, group_id: str, user_id: str, content: str, max_items: int = 20
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO long_term_memories(group_id, user_id, content) VALUES (?, ?, ?)",
                (group_id, user_id, content),
            )
            await db.execute(
                "DELETE FROM long_term_memories WHERE group_id = ? AND user_id = ? AND id NOT IN "
                "(SELECT id FROM long_term_memories WHERE group_id = ? AND user_id = ? "
                "ORDER BY id DESC LIMIT ?)",
                (group_id, user_id, group_id, user_id, max_items),
            )
            await db.commit()

    async def long_term_memories(
        self, group_id: str, user_id: str, limit: int = 20
    ) -> list[str]:
        rows = await self.fetchall(
            "SELECT content FROM (SELECT id, content FROM long_term_memories "
            "WHERE group_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (group_id, user_id, limit),
        )
        return [row[0] for row in rows]

    async def clear_long_term_memories(self, group_id: str, user_id: str) -> None:
        await self.execute(
            "DELETE FROM long_term_memories WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )

    async def record_group_activity(
        self, group_id: str, user_id: str, display_name: str, text: str, activity_date: str
    ) -> None:
        safe_terms = ("哈哈", "笑死", "确实", "绷不住", "好家伙", "逆天", "草", "乐", "行", "懂了")
        kaomoji_markers = ("(´", "(｀", "(^", "(￣", "(・", "(づ", "www")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO daily_activity(group_id, user_id, activity_date, message_count, display_name) "
                "VALUES (?, ?, ?, 1, ?) ON CONFLICT(group_id, user_id, activity_date) DO UPDATE SET "
                "message_count = message_count + 1, display_name = excluded.display_name",
                (group_id, user_id, activity_date, display_name),
            )
            await db.execute(
                "UPDATE group_member_identities SET display_name = ? "
                "WHERE group_id = ? AND user_id = ?",
                (display_name, group_id, user_id),
            )
            await db.execute(
                "INSERT INTO group_style_stats(group_id, sample_count, total_chars, question_count, "
                "exclamation_count, kaomoji_count) VALUES (?, 1, ?, ?, ?, ?) "
                "ON CONFLICT(group_id) DO UPDATE SET sample_count = sample_count + 1, "
                "total_chars = total_chars + excluded.total_chars, "
                "question_count = question_count + excluded.question_count, "
                "exclamation_count = exclamation_count + excluded.exclamation_count, "
                "kaomoji_count = kaomoji_count + excluded.kaomoji_count, updated_at = CURRENT_TIMESTAMP",
                (
                    group_id,
                    min(len(text), 500),
                    int("?" in text or "？" in text),
                    int("!" in text or "！" in text),
                    int(any(marker in text for marker in kaomoji_markers)),
                ),
            )
            for term in safe_terms:
                count = text.count(term)
                if count:
                    await db.execute(
                        "INSERT INTO group_style_terms(group_id, term, use_count) VALUES (?, ?, ?) "
                        "ON CONFLICT(group_id, term) DO UPDATE SET use_count = use_count + excluded.use_count",
                        (group_id, term, count),
                    )
            await db.commit()

    async def get_or_create_daily_possession(
        self,
        group_id: str,
        possession_date: str,
        minimum_messages: int = 15,
        reroll: bool = False,
    ) -> tuple[str, str, str] | None:
        existing = await self.fetchone(
            "SELECT user_id, display_name, mode FROM daily_possession "
            "WHERE group_id = ? AND possession_date = ?",
            (group_id, possession_date),
        )
        if existing and not reroll:
            if str(existing[2]) != "random":
                return str(existing[0]), str(existing[1]), str(existing[2])
            activity = await self.fetchone(
                "SELECT message_count FROM daily_activity WHERE group_id = ? "
                "AND user_id = ? AND activity_date = ?",
                (group_id, str(existing[0]), possession_date),
            )
            if activity and int(activity[0]) >= minimum_messages:
                return str(existing[0]), str(existing[1]), str(existing[2])
            await self.execute(
                "DELETE FROM daily_possession WHERE group_id = ? AND possession_date = ?",
                (group_id, possession_date),
            )
        candidates = await self.fetchall(
            "SELECT user_id, display_name FROM daily_activity WHERE group_id = ? "
            "AND activity_date = ? AND message_count >= ?",
            (group_id, possession_date, minimum_messages),
        )
        if not candidates:
            return None
        selectable = candidates
        if existing and len(candidates) > 1:
            selectable = [row for row in candidates if str(row[0]) != str(existing[0])]
        chosen = secrets.choice(selectable)
        await self.execute(
            "INSERT INTO daily_possession"
            "(group_id, possession_date, user_id, display_name, mode) VALUES (?, ?, ?, ?, 'random') "
            "ON CONFLICT(group_id, possession_date) DO UPDATE SET user_id = excluded.user_id, "
            "display_name = excluded.display_name, mode = 'random'",
            (group_id, possession_date, str(chosen[0]), str(chosen[1])),
        )
        if not existing or str(existing[0]) != str(chosen[0]) or str(existing[2]) != "random":
            await self.clear_possession_context(group_id, possession_date)
        saved = await self.fetchone(
            "SELECT user_id, display_name, mode FROM daily_possession "
            "WHERE group_id = ? AND possession_date = ?",
            (group_id, possession_date),
        )
        return (str(saved[0]), str(saved[1]), str(saved[2])) if saved else None

    async def set_targeted_possession(
        self, group_id: str, possession_date: str, user_id: str, display_name: str
    ) -> bool:
        current = await self.fetchone(
            "SELECT user_id, mode FROM daily_possession WHERE group_id = ? AND possession_date = ?",
            (group_id, possession_date),
        )
        await self.execute(
            "INSERT INTO daily_possession(group_id, possession_date, user_id, display_name, mode) "
            "VALUES (?, ?, ?, ?, 'targeted') ON CONFLICT(group_id, possession_date) DO UPDATE SET "
            "user_id = excluded.user_id, display_name = excluded.display_name, mode = 'targeted'",
            (group_id, possession_date, user_id, display_name),
        )
        if not current or str(current[0]) != user_id or str(current[1]) != "targeted":
            await self.clear_possession_context(group_id, possession_date)
        return True

    async def daily_possession(
        self, group_id: str, possession_date: str
    ) -> tuple[str, str, str] | None:
        row = await self.fetchone(
            "SELECT user_id, display_name, mode FROM daily_possession "
            "WHERE group_id = ? AND possession_date = ?",
            (group_id, possession_date),
        )
        return (str(row[0]), str(row[1]), str(row[2])) if row else None

    async def rename_daily_possession(
        self, group_id: str, possession_date: str, display_name: str
    ) -> None:
        await self.execute(
            "UPDATE daily_possession SET display_name = ? "
            "WHERE group_id = ? AND possession_date = ?",
            (display_name[:40], group_id, possession_date),
        )

    async def member_display_name(self, group_id: str, user_id: str) -> str | None:
        row = await self.fetchone(
            "SELECT display_name FROM daily_activity WHERE group_id = ? AND user_id = ? "
            "ORDER BY activity_date DESC LIMIT 1",
            (group_id, user_id),
        )
        return str(row[0]) if row else None

    async def possession_exited(self, group_id: str, possession_date: str) -> bool:
        row = await self.fetchone(
            "SELECT 1 FROM daily_possession_exits WHERE group_id = ? AND possession_date = ?",
            (group_id, possession_date),
        )
        return row is not None

    async def exit_daily_possession(
        self, group_id: str, possession_date: str, exited_by: str
    ) -> None:
        await self.execute(
            "DELETE FROM daily_possession WHERE group_id = ? AND possession_date = ?",
            (group_id, possession_date),
        )
        await self.execute(
            "DELETE FROM daily_possession_exits WHERE group_id = ? AND possession_date = ?",
            (group_id, possession_date),
        )
        await self.clear_possession_context(group_id, possession_date)

    async def possession_context_messages(
        self, group_id: str, possession_date: str, limit: int = 40
    ) -> list[dict[str, str]]:
        rows = await self.fetchall(
            "SELECT role, content FROM (SELECT id, role, content FROM possession_context "
            "WHERE group_id = ? AND possession_date = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (group_id, possession_date, limit),
        )
        return [{"role": str(row[0]), "content": str(row[1])} for row in rows]

    async def append_possession_exchange(
        self,
        group_id: str,
        possession_date: str,
        user_text: str,
        assistant_text: str,
        max_messages: int = 40,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executemany(
                "INSERT INTO possession_context(group_id, possession_date, role, content) "
                "VALUES (?, ?, ?, ?)",
                (
                    (group_id, possession_date, "user", user_text[:1000]),
                    (group_id, possession_date, "assistant", assistant_text[:2000]),
                ),
            )
            await db.execute(
                "DELETE FROM possession_context WHERE group_id = ? AND possession_date = ? "
                "AND id NOT IN (SELECT id FROM possession_context WHERE group_id = ? "
                "AND possession_date = ? ORDER BY id DESC LIMIT ?)",
                (group_id, possession_date, group_id, possession_date, max_messages),
            )
            await db.commit()

    async def clear_possession_context(self, group_id: str, possession_date: str) -> None:
        await self.execute(
            "DELETE FROM possession_context WHERE group_id = ? AND possession_date = ?",
            (group_id, possession_date),
        )

    async def possession_style_profile(
        self, group_id: str, user_id: str, max_age_hours: int | None = None
    ) -> str:
        sql = (
            "SELECT style_summary FROM possession_style_profiles "
            "WHERE group_id = ? AND user_id = ?"
        )
        params: tuple = (group_id, user_id)
        if max_age_hours is not None:
            sql += " AND updated_at >= datetime('now', ?)"
            params = (group_id, user_id, f"-{max(1, max_age_hours)} hours")
        row = await self.fetchone(sql, params)
        return str(row[0]) if row else ""

    async def save_possession_style_profile(
        self,
        group_id: str,
        user_id: str,
        display_name: str,
        style_summary: str,
        sample_count: int,
    ) -> None:
        await self.execute(
            "INSERT INTO possession_style_profiles"
            "(group_id, user_id, display_name, style_summary, sample_count) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(group_id, user_id) DO UPDATE SET "
            "display_name = excluded.display_name, style_summary = excluded.style_summary, "
            "sample_count = excluded.sample_count, updated_at = CURRENT_TIMESTAMP",
            (group_id, user_id, display_name, style_summary[:800], sample_count),
        )

    async def add_possession_style_message(
        self,
        group_id: str,
        user_id: str,
        display_name: str,
        content: str,
        has_image: bool = False,
        max_messages: int = 100,
    ) -> None:
        content = content.strip()[:500]
        if not content:
            return
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO possession_style_messages "
                "(group_id, user_id, display_name, content, has_image) VALUES (?, ?, ?, ?, ?)",
                (group_id, user_id, display_name[:80], content, int(has_image)),
            )
            await db.execute(
                "DELETE FROM possession_style_messages WHERE group_id = ? AND user_id = ? "
                "AND id NOT IN (SELECT id FROM possession_style_messages "
                "WHERE group_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?)",
                (group_id, user_id, group_id, user_id, max(10, max_messages)),
            )
            await db.commit()

    async def possession_style_messages(
        self, group_id: str, user_id: str, limit: int = 12
    ) -> list[str]:
        rows = await self.fetchall(
            "SELECT content FROM (SELECT id, content FROM possession_style_messages "
            "WHERE group_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (group_id, user_id, max(1, min(limit, 30))),
        )
        return [str(row[0]) for row in rows]

    async def possession_recall_messages(
        self, group_id: str, user_id: str, limit: int = 500
    ) -> list[str]:
        rows = await self.fetchall(
            "SELECT content FROM (SELECT id, content FROM possession_style_messages "
            "WHERE group_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (group_id, user_id, max(1, min(limit, 1000))),
        )
        return [str(row[0]) for row in rows]

    async def clear_possession_style(self, group_id: str, user_id: str) -> None:
        await self.execute(
            "DELETE FROM possession_style_profiles WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        await self.execute(
            "DELETE FROM possession_style_messages WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )

    async def get_or_create_daily_ultraman(
        self,
        group_id: str,
        user_id: str,
        draw_date: str,
        candidate_name: str,
    ) -> tuple[str, bool]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO daily_ultraman"
                "(user_id, draw_date, group_id, ultraman_name) VALUES (?, ?, ?, ?)",
                (user_id, draw_date, group_id, candidate_name),
            )
            created = cursor.rowcount > 0
            row = await (
                await db.execute(
                    "SELECT ultraman_name FROM daily_ultraman "
                    "WHERE user_id = ? AND draw_date = ?",
                    (user_id, draw_date),
                )
            ).fetchone()
            await db.commit()
        return str(row[0]), created

    async def ultraman_collection_stats(
        self, user_id: str
    ) -> tuple[int, int, str, int] | None:
        totals = await self.fetchone(
            "SELECT COUNT(*), COUNT(DISTINCT ultraman_name) "
            "FROM daily_ultraman WHERE user_id = ?",
            (user_id,),
        )
        if not totals or not totals[0]:
            return None
        favorite = await self.fetchone(
            "SELECT ultraman_name, COUNT(*) AS appearances "
            "FROM daily_ultraman WHERE user_id = ? "
            "GROUP BY ultraman_name ORDER BY appearances DESC, ultraman_name LIMIT 1",
            (user_id,),
        )
        return int(totals[0]), int(totals[1]), str(favorite[0]), int(favorite[1])

    async def get_or_create_daily_anime_character(
        self,
        group_id: str,
        user_id: str,
        draw_date: str,
        candidate_name: str,
    ) -> tuple[str, bool]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT OR IGNORE INTO daily_anime_character"
                "(user_id, draw_date, group_id, character_name) VALUES (?, ?, ?, ?)",
                (user_id, draw_date, group_id, candidate_name),
            )
            created = cursor.rowcount > 0
            row = await (
                await db.execute(
                    "SELECT character_name FROM daily_anime_character "
                    "WHERE user_id = ? AND draw_date = ?",
                    (user_id, draw_date),
                )
            ).fetchone()
            await db.commit()
        return str(row[0]), created

    async def anime_character_collection_stats(
        self, user_id: str
    ) -> tuple[int, int, str, int] | None:
        totals = await self.fetchone(
            "SELECT COUNT(*), COUNT(DISTINCT character_name) "
            "FROM daily_anime_character WHERE user_id = ?",
            (user_id,),
        )
        if not totals or not totals[0]:
            return None
        favorite = await self.fetchone(
            "SELECT character_name, COUNT(*) AS appearances "
            "FROM daily_anime_character WHERE user_id = ? "
            "GROUP BY character_name ORDER BY appearances DESC, character_name LIMIT 1",
            (user_id,),
        )
        return int(totals[0]), int(totals[1]), str(favorite[0]), int(favorite[1])

    async def add_group_member_identity(
        self,
        group_id: str,
        user_id: str,
        display_name: str,
        alias: str,
        max_items: int = 20,
    ) -> None:
        clean_alias = str(alias).strip()[:100]
        clean_name = str(display_name).strip()[:100]
        if not clean_alias:
            return
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO group_member_identities(group_id, user_id, display_name, alias) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(group_id, user_id, alias) DO UPDATE SET "
                "display_name = excluded.display_name, created_at = CURRENT_TIMESTAMP",
                (group_id, user_id, clean_name, clean_alias),
            )
            await db.execute(
                "DELETE FROM group_member_identities WHERE group_id = ? AND user_id = ? "
                "AND id NOT IN (SELECT id FROM group_member_identities "
                "WHERE group_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?)",
                (group_id, user_id, group_id, user_id, max_items),
            )
            await db.commit()

    async def group_member_identities(
        self, group_id: str, user_id: str, limit: int = 20
    ) -> list[str]:
        rows = await self.fetchall(
            "SELECT alias FROM group_member_identities "
            "WHERE group_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
            (group_id, user_id, limit),
        )
        return [str(row[0]) for row in rows]

    async def clear_group_member_identities(
        self, group_id: str, user_id: str
    ) -> None:
        await self.execute(
            "DELETE FROM group_member_identities WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )

    async def delete_group_member_identities_matching(
        self, group_id: str, text: str
    ) -> int:
        pattern = f"%{text}%"
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "DELETE FROM group_member_identities "
                "WHERE group_id = ? AND (alias LIKE ? OR user_id = ? OR display_name LIKE ?)",
                (group_id, pattern, text, pattern),
            )
            deleted = max(0, int(cursor.rowcount or 0))
            await db.commit()
        return deleted

    async def find_group_member_identity(
        self, group_id: str, value: str
    ) -> list[tuple[str, str, str]]:
        rows = await self.fetchall(
            "SELECT user_id, display_name, alias FROM group_member_identities "
            "WHERE group_id = ? ORDER BY id DESC",
            (group_id,),
        )
        needle = str(value).strip().casefold()
        matches: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for user_id, display_name, alias in rows:
            uid = str(user_id)
            name = str(display_name)
            label = str(alias)
            if needle not in {uid.casefold(), name.casefold(), label.casefold()}:
                continue
            key = (uid, label.casefold())
            if key in seen:
                continue
            seen.add(key)
            matches.append((uid, name, label))
        return matches

    async def latest_group_member_display_name(
        self, group_id: str, user_id: str
    ) -> str:
        row = await self.fetchone(
            "SELECT display_name FROM daily_activity "
            "WHERE group_id = ? AND user_id = ? "
            "ORDER BY activity_date DESC LIMIT 1",
            (group_id, user_id),
        )
        if row and row[0]:
            return str(row[0])
        row = await self.fetchone(
            "SELECT display_name FROM group_member_identities "
            "WHERE group_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
            (group_id, user_id),
        )
        return str(row[0]) if row and row[0] else ""

    async def add_group_memory(
        self, group_id: str, content: str, max_items: int = 50
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO group_memories(group_id, content) VALUES (?, ?)",
                (group_id, content),
            )
            await db.execute(
                "DELETE FROM group_memories WHERE group_id = ? AND id NOT IN "
                "(SELECT id FROM group_memories WHERE group_id = ? ORDER BY id DESC LIMIT ?)",
                (group_id, group_id, max_items),
            )
            await db.commit()

    async def group_memories(self, group_id: str, limit: int = 20) -> list[str]:
        rows = await self.fetchall(
            "SELECT content FROM (SELECT id, content FROM group_memories WHERE group_id = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id",
            (group_id, limit),
        )
        return [str(row[0]) for row in rows]

    async def clear_group_memories(self, group_id: str) -> None:
        await self.execute("DELETE FROM group_memories WHERE group_id = ?", (group_id,))

    async def delete_group_memories_matching(self, group_id: str, text: str) -> int:
        """Delete shared facts containing the exact text and drop transient context."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "DELETE FROM group_memories WHERE group_id = ? AND instr(content, ?) > 0",
                (group_id, text),
            )
            deleted = max(cursor.rowcount, 0)
            if deleted:
                await db.execute(
                    "DELETE FROM possession_context WHERE group_id = ?",
                    (group_id,),
                )
            await db.commit()
        return deleted

    async def group_style_hint(self, group_id: str) -> str:
        row = await self.fetchone(
            "SELECT sample_count, total_chars, question_count, exclamation_count, kaomoji_count "
            "FROM group_style_stats WHERE group_id = ?",
            (group_id,),
        )
        if not row or row[0] < 5:
            return ""
        samples, total_chars, questions, exclamations, kaomoji = row
        average = total_chars / samples
        length_style = "短句为主" if average < 18 else "中等长度" if average < 45 else "表达较完整"
        terms = await self.fetchall(
            "SELECT term FROM group_style_terms WHERE group_id = ? ORDER BY use_count DESC LIMIT 3",
            (group_id,),
        )
        details = [length_style]
        if questions / samples > 0.2:
            details.append("常用问句")
        if exclamations / samples > 0.2:
            details.append("语气较活跃")
        if kaomoji / samples > 0.1:
            details.append("偶尔使用颜文字")
        if terms:
            details.append("常见口头语：" + "、".join(term[0] for term in terms))
        return "；".join(details)
