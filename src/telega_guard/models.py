from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ChatSettings:
    chat_id: int
    chat_type: str
    title: str
    ban_if_used_before: bool = False
    ban_if_active_now: bool = False
    notify_admin_on_detection: bool = False
    enabled: bool = True

    @property
    def has_active_rules(self) -> bool:
        return self.enabled and (
            self.ban_if_used_before
            or self.ban_if_active_now
            or self.notify_admin_on_detection
        )


@dataclass(slots=True)
class JoinCandidate:
    chat_id: int
    chat_type: str
    user_id: int
    user_is_bot: bool = False
    title: str = ""
    source: str = "unknown"
    user_entity: Any | None = None
    discovered_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class TelegaCheckResult:
    user_id: int
    active_now: bool | None
    used_before: bool | None
    active_supported: bool
    active_source: str | None
    used_before_source: str | None
    checked_at: datetime = field(default_factory=utc_now)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ModerationDecision:
    should_ban: bool
    should_notify_admin: bool = False
    reason: str | None = None
    public_reason: str | None = None
    matched_statuses: tuple[str, ...] = ()
