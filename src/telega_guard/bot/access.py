from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.utils.chat_member import ADMINS
from aiogram.types import ChatMemberOwner

from telega_guard.models import ChatSettings
from telega_guard.repositories.chat_settings import ChatSettingsRepository

LOGGER = logging.getLogger(__name__)

_MEMBERSHIP_LOOKUP_INTERVAL_SECONDS = 0.1
_membership_lookup_lock = asyncio.Lock()
_membership_lookup_next_allowed_at = 0.0


async def is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    member = await _get_chat_member_with_backoff(bot, chat_id, user_id)
    if member is None:
        return False
    return isinstance(member, ADMINS)


async def is_chat_owner(bot: Bot, chat_id: int, user_id: int) -> bool:
    member = await _get_chat_member_with_backoff(bot, chat_id, user_id)
    if member is None:
        return False
    return isinstance(member, ChatMemberOwner)


async def owned_chats(
    repository: ChatSettingsRepository,
    bot: Bot,
    user_id: int,
) -> list[ChatSettings]:
    chats = await repository.iter_all()
    owned: list[ChatSettings] = []
    for settings in chats:
        if await is_chat_owner(bot, settings.chat_id, user_id):
            owned.append(settings)
    return owned


async def administered_chats(
    repository: ChatSettingsRepository,
    bot: Bot,
    user_id: int,
) -> list[ChatSettings]:
    chats = await repository.iter_all()
    administered: list[ChatSettings] = []
    for settings in chats:
        if await is_chat_admin(bot, settings.chat_id, user_id):
            administered.append(settings)
    return administered


async def has_private_access(
    repository: ChatSettingsRepository,
    bot: Bot,
    user_id: int,
    *,
    owner_user_id: int | None = None,
) -> bool:
    if owner_user_id is not None and user_id == owner_user_id:
        return True
    return bool(await owned_chats(repository, bot, user_id))


async def _get_chat_member_with_backoff(bot: Bot, chat_id: int, user_id: int):
    for _attempt in range(3):
        await _wait_for_membership_slot()
        try:
            return await bot.get_chat_member(chat_id, user_id)
        except TelegramRetryAfter as exc:
            delay = max(float(exc.retry_after), _MEMBERSHIP_LOOKUP_INTERVAL_SECONDS)
            LOGGER.warning(
                "Telegram requested backoff for membership checks: %ss (chat=%s user=%s)",
                delay,
                chat_id,
                user_id,
            )
            await _defer_membership_checks(delay)
        except Exception:
            return None
    return None


async def _wait_for_membership_slot() -> None:
    global _membership_lookup_next_allowed_at

    async with _membership_lookup_lock:
        now = time.monotonic()
        wait_seconds = _membership_lookup_next_allowed_at - now
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        _membership_lookup_next_allowed_at = (
            time.monotonic() + _MEMBERSHIP_LOOKUP_INTERVAL_SECONDS
        )


async def _defer_membership_checks(delay_seconds: float) -> None:
    global _membership_lookup_next_allowed_at

    async with _membership_lookup_lock:
        target = time.monotonic() + delay_seconds
        if target > _membership_lookup_next_allowed_at:
            _membership_lookup_next_allowed_at = target

    await asyncio.sleep(delay_seconds)
