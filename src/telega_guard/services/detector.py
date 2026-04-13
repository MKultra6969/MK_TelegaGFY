from __future__ import annotations

import logging
import time
from typing import Any

from telethon import functions, errors
from telethon.client.telegramclient import TelegramClient

from telega_guard.models import TelegaCheckResult
from telega_guard.services.lookup import CallsLookupService

LOGGER = logging.getLogger(__name__)


class TelethonDetectorService:
    def __init__(self, client: TelegramClient, lookup_service: CallsLookupService) -> None:
        self.client = client
        self.lookup_service = lookup_service
        self._entity_cache: dict[tuple[int, int], tuple[Any, float]] = {}
        self._entity_cache_ttl_seconds = 600
        self._active_lookup_cooldown_until = 0.0

    def remember_entity(self, chat_id: int, user_entity: Any) -> None:
        user_id = int(getattr(user_entity, "id", 0) or 0)
        if chat_id == 0 or user_id <= 0:
            return
        self._entity_cache[(chat_id, user_id)] = (user_entity, time.monotonic())
        self._prune_entity_cache()

    async def check_user(
        self,
        *,
        chat_id: int,
        user_id: int,
        user_entity: Any | None = None,
    ) -> TelegaCheckResult:
        notes: list[str] = []
        active_now, active_supported = await self._check_active_now(
            chat_id=chat_id,
            user_id=user_id,
            user_entity=user_entity,
            notes=notes,
        )
        used_before = await self.lookup_service.lookup_used_before(user_id)
        if used_before is None:
            notes.append("historical lookup unavailable")

        return TelegaCheckResult(
            user_id=user_id,
            active_now=active_now,
            used_before=used_before,
            active_supported=active_supported,
            active_source=(
                "users.getFullUser.unofficial_security_risk"
                if active_supported
                else None
            ),
            used_before_source="ok_calls_lookup" if used_before is not None else None,
            notes=notes,
        )

    async def _check_active_now(
        self,
        *,
        chat_id: int,
        user_id: int,
        user_entity: Any | None,
        notes: list[str],
    ) -> tuple[bool | None, bool]:
        if self._active_lookup_cooldown_until > time.monotonic():
            notes.append("active-session lookup is cooling down after flood wait")
            return None, False

        entity = await self._resolve_entity(chat_id=chat_id, user_id=user_id, user_entity=user_entity)
        if entity is None:
            notes.append("telethon entity was not resolved")
            return None, False

        try:
            full = await self.client(functions.users.GetFullUserRequest(id=entity))
        except errors.FloodWaitError as exc:
            self._active_lookup_cooldown_until = max(
                self._active_lookup_cooldown_until,
                time.monotonic() + exc.seconds,
            )
            notes.append(f"flood wait {exc.seconds}s during active-session check")
            return None, False
        except Exception:
            LOGGER.exception("Active Telega lookup failed for user %s", user_id)
            notes.append("users.getFullUser failed")
            return None, False

        for candidate in (
            getattr(full, "full_user", None),
            getattr(full, "user_full", None),
            full,
        ):
            flag = self._extract_unofficial_security_flag(candidate)
            if flag is not None:
                return flag, True

        notes.append("unofficial_security_risk is not exposed by current Telethon layer")
        return None, False

    async def _resolve_entity(self, *, chat_id: int, user_id: int, user_entity: Any | None) -> Any | None:
        if user_entity is not None:
            self.remember_entity(chat_id, user_entity)
            return user_entity

        self._prune_entity_cache()
        cached = self._entity_cache.get((chat_id, user_id))
        if cached is not None:
            return cached[0]

        try:
            entity = await self.client.get_entity(user_id)
        except Exception:
            entity = None
        else:
            self.remember_entity(chat_id, entity)
            return entity

        try:
            chat = await self.client.get_entity(chat_id)
            async for participant in self.client.iter_participants(chat, limit=200):
                if int(getattr(participant, "id", 0) or 0) == user_id:
                    self.remember_entity(chat_id, participant)
                    return participant
        except Exception:
            LOGGER.debug("Could not resolve participant %s in chat %s", user_id, chat_id, exc_info=True)
        return None

    def _prune_entity_cache(self) -> None:
        now = time.monotonic()
        stale = [
            key
            for key, (_, ts) in self._entity_cache.items()
            if (now - ts) > self._entity_cache_ttl_seconds
        ]
        for key in stale:
            self._entity_cache.pop(key, None)

    def _extract_unofficial_security_flag(self, payload: Any) -> bool | None:
        return _deep_find_unofficial_flag(payload, set())


def _deep_find_unofficial_flag(payload: Any, seen: set[int]) -> bool | None:
    if payload is None:
        return None

    marker = id(payload)
    if marker in seen:
        return None
    seen.add(marker)

    for attr_name in ("unofficial_security_risk", "unofficialSecurityRisk"):
        try:
            if hasattr(payload, attr_name):
                value = getattr(payload, attr_name)
                if isinstance(value, bool):
                    return value
                if value in (0, 1):
                    return bool(value)
        except Exception:
            continue

    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).strip().lower()
            if normalized in {"unofficial_security_risk", "unofficialsecurityrisk"}:
                if isinstance(value, bool):
                    return value
                if value in (0, 1):
                    return bool(value)
            result = _deep_find_unofficial_flag(value, seen)
            if result is not None:
                return result
        return None

    if isinstance(payload, (list, tuple, set)):
        for item in payload:
            result = _deep_find_unofficial_flag(item, seen)
            if result is not None:
                return result
        return None

    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            result = _deep_find_unofficial_flag(to_dict(), seen)
            if result is not None:
                return result
        except Exception:
            pass

    if hasattr(payload, "__dict__"):
        try:
            result = _deep_find_unofficial_flag(vars(payload), seen)
            if result is not None:
                return result
        except Exception:
            pass

    return None
