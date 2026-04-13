from __future__ import annotations

from dataclasses import dataclass
from time import time

from telega_guard.db import Database


@dataclass(slots=True)
class LookupCacheEntry:
    user_id: int
    used_before: bool | None
    checked_at: int
    failure_at: int
    failure_retry_after: int


class LookupCacheRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_entry(self, user_id: int) -> LookupCacheEntry | None:
        cursor = await self.db.connection.execute(
            """
            SELECT user_id, used_before, checked_at, failure_at, failure_retry_after
            FROM lookup_cache
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return LookupCacheEntry(
            user_id=row["user_id"],
            used_before=None if row["used_before"] is None else bool(row["used_before"]),
            checked_at=int(row["checked_at"] or 0),
            failure_at=int(row["failure_at"] or 0),
            failure_retry_after=int(row["failure_retry_after"] or 0),
        )

    async def set_result(self, user_id: int, used_before: bool) -> None:
        now = int(time())
        await self.db.connection.execute(
            """
            INSERT INTO lookup_cache (user_id, used_before, checked_at, failure_at, failure_retry_after)
            VALUES (?, ?, ?, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                used_before = excluded.used_before,
                checked_at = excluded.checked_at,
                failure_at = 0,
                failure_retry_after = 0
            """,
            (user_id, 1 if used_before else 0, now),
        )
        await self.db.connection.commit()

    async def set_failure(self, user_id: int, retry_after_ts: int) -> None:
        now = int(time())
        await self.db.connection.execute(
            """
            INSERT INTO lookup_cache (user_id, used_before, checked_at, failure_at, failure_retry_after)
            VALUES (?, NULL, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                failure_at = excluded.failure_at,
                failure_retry_after = excluded.failure_retry_after
            """,
            (user_id, now, retry_after_ts),
        )
        await self.db.connection.commit()

    async def clear_failure(self, user_id: int) -> None:
        await self.db.connection.execute(
            """
            UPDATE lookup_cache
            SET failure_at = 0, failure_retry_after = 0
            WHERE user_id = ?
            """,
            (user_id,),
        )
        await self.db.connection.commit()
