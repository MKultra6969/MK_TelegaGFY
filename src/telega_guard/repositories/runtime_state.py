from __future__ import annotations

from telega_guard.db import Database


class RuntimeStateRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_last_admin_log_event_id(self, chat_id: int) -> int:
        cursor = await self.db.connection.execute(
            "SELECT last_admin_log_event_id FROM chat_runtime_state WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return int(row["last_admin_log_event_id"]) if row else 0

    async def set_last_admin_log_event_id(self, chat_id: int, event_id: int) -> None:
        await self.db.connection.execute(
            """
            INSERT INTO chat_runtime_state (chat_id, last_admin_log_event_id)
            VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                last_admin_log_event_id = excluded.last_admin_log_event_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, event_id),
        )
        await self.db.connection.commit()
