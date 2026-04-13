from __future__ import annotations

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._connection: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not connected")
        return self._connection

    async def connect(self) -> None:
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL;")
        await self.connection.execute("PRAGMA foreign_keys=ON;")
        await self.connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def init_schema(self) -> None:
        await self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                chat_type TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                ban_if_used_before INTEGER NOT NULL DEFAULT 0,
                ban_if_active_now INTEGER NOT NULL DEFAULT 0,
                notify_admin_on_detection INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS lookup_cache (
                user_id INTEGER PRIMARY KEY,
                used_before INTEGER,
                checked_at INTEGER,
                failure_at INTEGER,
                failure_retry_after INTEGER
            );

            CREATE TABLE IF NOT EXISTS chat_runtime_state (
                chat_id INTEGER PRIMARY KEY,
                last_admin_log_event_id INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS moderation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                result TEXT NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await self._ensure_column(
            table_name="chat_settings",
            column_name="notify_admin_on_detection",
            definition="INTEGER NOT NULL DEFAULT 0",
        )
        await self.connection.commit()

    async def _ensure_column(
        self,
        *,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        cursor = await self.connection.execute(f"PRAGMA table_info({table_name})")
        rows = await cursor.fetchall()
        if any(row["name"] == column_name for row in rows):
            return
        await self.connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )


SQLiteDatabase = Database
