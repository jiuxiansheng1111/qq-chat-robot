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
                """
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
