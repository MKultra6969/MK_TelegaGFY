from __future__ import annotations

from telega_guard.db import Database


class PrivateUsersRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._schema_ready = False

    async def upsert_user(self, user_id: int) -> None:
        await self._ensure_schema()
        await self.db.connection.execute(
            """
            INSERT INTO private_users (user_id, updated_at)
            VALUES (?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                updated_at = excluded.updated_at
            """,
            (user_id,),
        )
        await self.db.connection.commit()

    async def touch_user(self, user_id: int) -> None:
        await self.upsert_user(user_id)

    async def list_recipient_user_ids(self) -> list[int]:
        await self._ensure_schema()
        cursor = await self.db.connection.execute(
            """
            SELECT user_id
            FROM private_users
            ORDER BY user_id
            """
        )
        rows = await cursor.fetchall()
        return [int(row["user_id"]) for row in rows]

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        await self.db.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS private_users (
                user_id INTEGER PRIMARY KEY,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await self.db.connection.commit()
        self._schema_ready = True
