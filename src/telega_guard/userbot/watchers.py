from __future__ import annotations

import logging

from telethon import events
from telethon.tl import types as tl_types

from telega_guard.models import JoinCandidate
from telega_guard.repositories.chat_settings import ChatSettingsRepository
from telega_guard.services.detector import TelethonDetectorService
from telega_guard.services.moderation import ModerationCoordinator

LOGGER = logging.getLogger(__name__)


class TelethonWatcher:
    def __init__(
        self,
        *,
        repository: ChatSettingsRepository,
        detector: TelethonDetectorService,
        moderation: ModerationCoordinator,
    ) -> None:
        self.repository = repository
        self.detector = detector
        self.moderation = moderation
        self._installed = False

    def install(self, client) -> None:
        if self._installed:
            return
        client.add_event_handler(self._handle_chat_action, events.ChatAction())
        self._installed = True

    def uninstall(self, client) -> None:
        if not self._installed:
            return
        client.remove_event_handler(self._handle_chat_action)
        self._installed = False

    async def _handle_chat_action(self, event: events.ChatAction.Event) -> None:
        if not (event.user_joined or event.user_added):
            return
        if not event.chat_id:
            return

        try:
            chat = await event.get_chat()
            users = await event.get_users()
        except Exception:
            LOGGER.debug("Failed to expand Telethon chat action event", exc_info=True)
            return

        if not users:
            return

        chat_type = _telethon_chat_type(chat)
        title = str(getattr(chat, "title", None) or getattr(chat, "username", None) or event.chat_id)
        await self.repository.upsert_chat(event.chat_id, chat_type, title)

        for user in users:
            LOGGER.info(
                "telethon_chat_action_join chat_id=%s chat_type=%s user_id=%s source=telethon_chat_action",
                event.chat_id,
                chat_type,
                int(getattr(user, "id", 0) or 0),
            )
            self.detector.remember_entity(event.chat_id, user)
            await self.moderation.enqueue(
                JoinCandidate(
                    chat_id=event.chat_id,
                    chat_type=chat_type,
                    user_id=int(getattr(user, "id", 0) or 0),
                    user_is_bot=bool(getattr(user, "bot", False)),
                    title=title,
                    source="telethon_chat_action",
                    user_entity=user,
                )
            )


def _telethon_chat_type(chat) -> str:
    if isinstance(chat, tl_types.Channel):
        if bool(getattr(chat, "broadcast", False)):
            return "channel"
        return "supergroup" if bool(getattr(chat, "megagroup", False)) else "channel"
    if isinstance(chat, tl_types.Chat):
        return "group"
    return "unknown"
