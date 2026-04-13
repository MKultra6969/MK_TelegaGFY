from __future__ import annotations

from telega_guard.db import Database
from telega_guard.models import ChatSettings


class ChatSettingsRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert_chat(self, chat_id: int, chat_type: str, title: str) -> ChatSettings:
        await self.db.connection.execute(
            """
            INSERT INTO chat_settings (chat_id, chat_type, title)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat_type = excluded.chat_type,
                title = excluded.title,
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, chat_type, title),
        )
        await self.db.connection.commit()
        settings = await self.get(chat_id)
        if settings is None:
            raise RuntimeError(f"Failed to upsert chat settings for {chat_id}")
        return settings

    async def get(self, chat_id: int) -> ChatSettings | None:
        cursor = await self.db.connection.execute(
            """
            SELECT
                chat_id,
                chat_type,
                title,
                ban_if_used_before,
                ban_if_active_now,
                notify_admin_on_detection,
                enabled
            FROM chat_settings
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return ChatSettings(
            chat_id=row["chat_id"],
            chat_type=row["chat_type"],
            title=row["title"],
            ban_if_used_before=bool(row["ban_if_used_before"]),
            ban_if_active_now=bool(row["ban_if_active_now"]),
            notify_admin_on_detection=bool(row["notify_admin_on_detection"]),
            enabled=bool(row["enabled"]),
        )

    async def set_flag(self, chat_id: int, flag: str, value: bool) -> ChatSettings | None:
        if flag not in {
            "ban_if_used_before",
            "ban_if_active_now",
            "notify_admin_on_detection",
        }:
            raise ValueError(f"Unsupported flag: {flag}")
        await self.db.connection.execute(
            f"""
            UPDATE chat_settings
            SET {flag} = ?, updated_at = CURRENT_TIMESTAMP
            WHERE chat_id = ?
            """,
            (1 if value else 0, chat_id),
        )
        await self.db.connection.commit()
        return await self.get(chat_id)

    async def iter_all(self) -> list[ChatSettings]:
        cursor = await self.db.connection.execute(
            """
            SELECT
                chat_id,
                chat_type,
                title,
                ban_if_used_before,
                ban_if_active_now,
                notify_admin_on_detection,
                enabled
            FROM chat_settings
            ORDER BY title COLLATE NOCASE, chat_id
            """
        )
        rows = await cursor.fetchall()
        return [
            ChatSettings(
                chat_id=row["chat_id"],
                chat_type=row["chat_type"],
                title=row["title"],
                ban_if_used_before=bool(row["ban_if_used_before"]),
                ban_if_active_now=bool(row["ban_if_active_now"]),
                notify_admin_on_detection=bool(row["notify_admin_on_detection"]),
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]

    async def iter_monitored_channels(self) -> list[ChatSettings]:
        cursor = await self.db.connection.execute(
            """
            SELECT
                chat_id,
                chat_type,
                title,
                ban_if_used_before,
                ban_if_active_now,
                notify_admin_on_detection,
                enabled
            FROM chat_settings
            WHERE enabled = 1
              AND chat_type = 'channel'
              AND (
                  ban_if_used_before = 1
                  OR ban_if_active_now = 1
                  OR notify_admin_on_detection = 1
              )
            ORDER BY chat_id
            """
        )
        rows = await cursor.fetchall()
        return [
            ChatSettings(
                chat_id=row["chat_id"],
                chat_type=row["chat_type"],
                title=row["title"],
                ban_if_used_before=bool(row["ban_if_used_before"]),
                ban_if_active_now=bool(row["ban_if_active_now"]),
                notify_admin_on_detection=bool(row["notify_admin_on_detection"]),
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]
