from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, TelegramObject

LOGGER = logging.getLogger(__name__)
_TEXT_PREVIEW_LIMIT = 120


class InteractionLoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        payload = describe_interaction(event)
        if payload is not None:
            LOGGER.info(payload)
        return await handler(event, data)


def describe_interaction(event: TelegramObject) -> str | None:
    if isinstance(event, Message) or _looks_like_message(event):
        return _describe_message(event)
    if isinstance(event, CallbackQuery) or _looks_like_callback(event):
        return _describe_callback(event)
    if isinstance(event, ChatMemberUpdated) or _looks_like_chat_member_update(event):
        return _describe_chat_member_update(event)
    return None


def _describe_message(message: Message) -> str:
    chat_type = getattr(message.chat, "type", "unknown")
    chat_id = getattr(message.chat, "id", "?")
    user_id = getattr(message.from_user, "id", None)
    text = _truncate_text(message.text or message.caption)

    if message.new_chat_members:
        joined_ids = ",".join(str(user.id) for user in message.new_chat_members)
        return (
            "incoming_message"
            f" kind=new_chat_members chat_type={chat_type} chat_id={chat_id}"
            f" actor_id={user_id} joined_ids={joined_ids}"
        )

    command = _extract_command(message.text)
    if command is not None:
        return (
            "incoming_message"
            f" kind=command command={command} chat_type={chat_type}"
            f" chat_id={chat_id} user_id={user_id} text={text}"
        )

    return (
        "incoming_message"
        f" kind=text chat_type={chat_type} chat_id={chat_id}"
        f" user_id={user_id} text={text}"
    )


def _describe_callback(query: CallbackQuery) -> str:
    message = query.message
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    message_id = getattr(message, "message_id", None)
    return (
        "incoming_callback"
        f" user_id={getattr(query.from_user, 'id', None)}"
        f" chat_id={chat_id} message_id={message_id}"
        f" data={_truncate_text(query.data)}"
    )


def _describe_chat_member_update(update: ChatMemberUpdated) -> str:
    actor_id = getattr(getattr(update, "from_user", None), "id", None)
    target_user_id = getattr(getattr(update.new_chat_member, "user", None), "id", None)
    return (
        "membership_update"
        f" chat_type={getattr(update.chat, 'type', 'unknown')}"
        f" chat_id={getattr(update.chat, 'id', None)}"
        f" actor_id={actor_id} target_user_id={target_user_id}"
        f" old_status={getattr(update.old_chat_member, 'status', None)}"
        f" new_status={getattr(update.new_chat_member, 'status', None)}"
    )


def _extract_command(text: str | None) -> str | None:
    if not text or not text.startswith("/"):
        return None
    return text.split(maxsplit=1)[0]


def _truncate_text(text: str | None) -> str:
    if not text:
        return "-"
    compact = " ".join(text.split())
    if len(compact) <= _TEXT_PREVIEW_LIMIT:
        return compact
    return f"{compact[:_TEXT_PREVIEW_LIMIT - 3]}..."


def _looks_like_message(event: Any) -> bool:
    return hasattr(event, "chat") and hasattr(event, "from_user") and hasattr(event, "new_chat_members")


def _looks_like_callback(event: Any) -> bool:
    return hasattr(event, "data") and hasattr(event, "message") and hasattr(event, "from_user")


def _looks_like_chat_member_update(event: Any) -> bool:
    return (
        hasattr(event, "chat")
        and hasattr(event, "old_chat_member")
        and hasattr(event, "new_chat_member")
    )
