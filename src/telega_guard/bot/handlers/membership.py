from __future__ import annotations

import logging

from aiogram import Router
from aiogram import F
from aiogram.types import ChatMemberUpdated, Message

from telega_guard.models import JoinCandidate
from telega_guard.repositories.chat_settings import ChatSettingsRepository
from telega_guard.services.moderation import ModerationCoordinator

LOGGER = logging.getLogger(__name__)


def create_membership_router(
    repository: ChatSettingsRepository,
    moderation: ModerationCoordinator,
) -> Router:
    router = Router(name="membership")

    @router.message(F.new_chat_members)
    async def handle_new_chat_members(message: Message) -> None:
        if message.chat.type == "private":
            return

        LOGGER.info(
            "bot_api_join_message chat_id=%s chat_type=%s actor_id=%s joined_count=%s",
            message.chat.id,
            message.chat.type,
            getattr(message.from_user, "id", None),
            len(message.new_chat_members),
        )
        await repository.upsert_chat(message.chat.id, message.chat.type, _chat_title(message))
        for user in message.new_chat_members:
            await moderation.enqueue(
                JoinCandidate(
                    chat_id=message.chat.id,
                    chat_type=message.chat.type,
                    user_id=user.id,
                    user_is_bot=bool(user.is_bot),
                    title=_chat_title(message),
                    source="aiogram_new_chat_members",
                )
            )

    @router.chat_member()
    async def handle_chat_member(update: ChatMemberUpdated) -> None:
        if update.chat.type == "private":
            return
        if not _became_member(update):
            return

        user = update.new_chat_member.user
        LOGGER.info(
            "bot_api_chat_member_join chat_id=%s chat_type=%s actor_id=%s target_user_id=%s",
            update.chat.id,
            update.chat.type,
            getattr(update.from_user, "id", None),
            user.id,
        )
        await repository.upsert_chat(update.chat.id, update.chat.type, _chat_title(update))
        await moderation.enqueue(
            JoinCandidate(
                chat_id=update.chat.id,
                chat_type=update.chat.type,
                user_id=user.id,
                user_is_bot=bool(user.is_bot),
                title=_chat_title(update),
                source="aiogram_chat_member",
            )
        )

    return router


def _became_member(update: ChatMemberUpdated) -> bool:
    present = {"member", "administrator", "restricted", "creator", "owner"}
    absent = {"left", "kicked"}
    old_status = str(update.old_chat_member.status)
    new_status = str(update.new_chat_member.status)
    return old_status in absent and new_status in present


def _chat_title(update: Message | ChatMemberUpdated) -> str:
    chat = update.chat
    return str(chat.title or getattr(chat, "full_name", None) or chat.id)
