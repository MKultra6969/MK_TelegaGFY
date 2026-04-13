from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import errors, functions, types
from telethon.client.telegramclient import TelegramClient

from telega_guard.models import JoinCandidate
from telega_guard.repositories.chat_settings import ChatSettingsRepository
from telega_guard.repositories.runtime_state import RuntimeStateRepository
from telega_guard.services.detector import TelethonDetectorService
from telega_guard.services.moderation import ModerationCoordinator

LOGGER = logging.getLogger(__name__)
ADMIN_LOG_PAGE_SIZE = 100


class ChannelAdminLogPoller:
    def __init__(
        self,
        *,
        client: TelegramClient,
        settings_repository: ChatSettingsRepository,
        runtime_repository: RuntimeStateRepository,
        detector: TelethonDetectorService,
        moderation: ModerationCoordinator,
        poll_interval_seconds: int,
    ) -> None:
        self.client = client
        self.settings_repository = settings_repository
        self.runtime_repository = runtime_repository
        self.detector = detector
        self.moderation = moderation
        self.poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="channel-admin-log-poller")

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                channels = await self.settings_repository.iter_monitored_channels()
                for settings in channels:
                    await self._poll_channel(settings.chat_id, settings.title)
            except Exception:
                LOGGER.exception("Channel admin log polling failed")
            await asyncio.sleep(self.poll_interval_seconds)

    async def _poll_channel(self, chat_id: int, title: str) -> None:
        last_seen = await self.runtime_repository.get_last_admin_log_event_id(chat_id)
        channel = await self.client.get_input_entity(chat_id)
        entries: list[Any] = []
        users_by_id: dict[int, Any] = {}
        request_max_id = 0
        newest_id = last_seen

        while True:
            try:
                result = await self.client(
                    functions.channels.GetAdminLogRequest(
                        channel=channel,
                        q="",
                        max_id=request_max_id,
                        min_id=last_seen,
                        limit=ADMIN_LOG_PAGE_SIZE,
                        events_filter=types.ChannelAdminLogEventsFilter(
                            join=True,
                            invite=True,
                        ),
                    )
                )
            except errors.FloodWaitError as exc:
                wait_seconds = max(int(getattr(exc, "seconds", 0) or 0), 1)
                LOGGER.warning(
                    "Flood wait while polling admin log for chat %s; retrying in %s seconds",
                    chat_id,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
                continue

            page_entries = [
                entry
                for entry in (result.events or [])
                if int(getattr(entry, "id", 0) or 0) > last_seen
            ]
            if not page_entries:
                break

            for user in (result.users or []):
                user_id = int(getattr(user, "id", 0) or 0)
                if user_id > 0:
                    users_by_id[user_id] = user

            entries.extend(page_entries)
            page_event_ids = [int(getattr(entry, "id", 0) or 0) for entry in page_entries]
            newest_id = max(newest_id, max(page_event_ids))
            page_oldest_id = min(page_event_ids)

            if len(page_entries) < ADMIN_LOG_PAGE_SIZE:
                break
            request_max_id = page_oldest_id

        if not entries:
            return

        for entry in sorted(entries, key=lambda item: int(getattr(item, "id", 0) or 0)):
            event_id = int(getattr(entry, "id", 0) or 0)
            newest_id = max(newest_id, event_id)
            for user_id in _extract_candidate_user_ids(entry):
                user_entity = users_by_id.get(user_id)
                if user_entity is not None:
                    self.detector.remember_entity(chat_id, user_entity)
                await self.moderation.enqueue(
                    JoinCandidate(
                        chat_id=chat_id,
                        chat_type="channel",
                        user_id=user_id,
                        user_is_bot=bool(getattr(user_entity, "bot", False)) if user_entity else False,
                        title=title,
                        source="telethon_admin_log",
                        user_entity=user_entity,
                    )
                )

        if newest_id > last_seen:
            await self.runtime_repository.set_last_admin_log_event_id(chat_id, newest_id)


def _extract_candidate_user_ids(entry: Any) -> list[int]:
    action = getattr(entry, "action", None)
    if isinstance(action, types.ChannelAdminLogEventActionParticipantInvite):
        participant_id = _extract_participant_user_id(getattr(action, "participant", None))
        if participant_id:
            return [participant_id]

    actor_id = int(getattr(entry, "user_id", 0) or 0)
    ids = set(_deep_collect_user_ids(action, set()))
    ids.discard(0)
    if ids:
        if actor_id and actor_id in ids and len(ids) > 1:
            ids.discard(actor_id)
        return sorted(ids)
    return [actor_id] if actor_id else []


def _extract_participant_user_id(participant: Any) -> int:
    if participant is None:
        return 0

    if isinstance(participant, dict):
        for key in ("user_id", "id"):
            user_id = int(participant.get(key, 0) or 0)
            if user_id > 0:
                return user_id
        peer = participant.get("peer")
        if isinstance(peer, dict) and str(peer.get("_", "")).lower() == "peeruser":
            return int(peer.get("user_id", 0) or 0)
        return 0

    for attr_name in ("user_id", "id"):
        user_id = int(getattr(participant, attr_name, 0) or 0)
        if user_id > 0:
            return user_id

    peer = getattr(participant, "peer", None)
    if isinstance(peer, types.PeerUser):
        return int(getattr(peer, "user_id", 0) or 0)

    to_dict = getattr(participant, "to_dict", None)
    if callable(to_dict):
        try:
            return _extract_participant_user_id(to_dict())
        except Exception:
            pass

    return 0


def _deep_collect_user_ids(payload: Any, seen: set[int]) -> set[int]:
    result: set[int] = set()
    if payload is None:
        return result

    marker = id(payload)
    if marker in seen:
        return result
    seen.add(marker)

    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).strip().lower()
            if normalized == "user_id" and isinstance(value, int):
                result.add(value)
            if normalized == "peer" and isinstance(value, dict):
                if str(value.get("_", "")).lower() == "peeruser":
                    peer_user_id = int(value.get("user_id", 0) or 0)
                    if peer_user_id > 0:
                        result.add(peer_user_id)
            result.update(_deep_collect_user_ids(value, seen))
        return result

    if isinstance(payload, (list, tuple, set)):
        for item in payload:
            result.update(_deep_collect_user_ids(item, seen))
        return result

    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            result.update(_deep_collect_user_ids(to_dict(), seen))
        except Exception:
            pass
        return result

    if hasattr(payload, "__dict__"):
        try:
            result.update(_deep_collect_user_ids(vars(payload), seen))
        except Exception:
            pass
    return result
