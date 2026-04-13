from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from telega_guard.db import Database


@dataclass(slots=True)
class ModerationEvent:
    id: int
    chat_id: int
    user_id: int
    action: str
    reason: str
    result: str
    details: dict[str, Any]
    created_at: str


class ModerationEventRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add_event(
        self,
        *,
        chat_id: int,
        user_id: int,
        action: str,
        reason: str,
        result: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self.db.connection.execute(
            """
            INSERT INTO moderation_events (chat_id, user_id, action, reason, result, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                user_id,
                action,
                reason,
                result,
                json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        await self.db.connection.commit()

    async def list_events(self, chat_id: int, *, limit: int = 10) -> list[ModerationEvent]:
        cursor = await self.db.connection.execute(
            """
            SELECT id, chat_id, user_id, action, reason, result, details_json, created_at
            FROM moderation_events
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        events: list[ModerationEvent] = []
        for row in rows:
            raw_details = row["details_json"] or "{}"
            try:
                details = json.loads(raw_details)
            except json.JSONDecodeError:
                details = {"raw_details": raw_details}
            events.append(
                ModerationEvent(
                    id=int(row["id"]),
                    chat_id=int(row["chat_id"]),
                    user_id=int(row["user_id"]),
                    action=str(row["action"]),
                    reason=str(row["reason"]),
                    result=str(row["result"]),
                    details=details,
                    created_at=str(row["created_at"]),
                )
            )
        return events
