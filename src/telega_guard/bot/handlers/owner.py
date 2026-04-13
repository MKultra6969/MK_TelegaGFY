from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from typing import Literal

from aiogram import Bot, Router, html
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import CallbackQuery, Message

from telega_guard.bot.callbacks import BroadcastCallback
from telega_guard.bot.keyboards import broadcast_confirmation_keyboard
from telega_guard.models import TelegaCheckResult
from telega_guard.repositories.private_users import PrivateUsersRepository
from telega_guard.services.detector import TelethonDetectorService

LOGGER = logging.getLogger(__name__)

_MEDIA_GROUP_DEBOUNCE_SECONDS = 0.8


@dataclass(slots=True)
class BroadcastDraft:
    source_chat_id: int
    message_ids: tuple[int, ...]
    token: str
    preview_message_ids: tuple[int, ...] = ()
    confirmation_message_id: int | None = None


@dataclass(slots=True)
class BroadcastSession:
    status: Literal["awaiting_content", "collecting_album", "awaiting_confirmation", "sending"]
    draft: BroadcastDraft | None = None
    media_group_id: str | None = None
    media_group_message_ids: list[int] = field(default_factory=list)
    finalize_task: asyncio.Task[None] | None = None


def create_owner_router(
    detector: TelethonDetectorService,
    recipient_repository: PrivateUsersRepository,
    owner_user_id: int | None,
) -> Router:
    router = Router(name="owner")
    sessions: dict[int, BroadcastSession] = {}

    @router.message(Command("check_user"))
    async def handle_check_user(message: Message, command: CommandObject | None = None) -> None:
        if not _is_owner_message(message, owner_user_id):
            await message.answer(
                "<b>Доступ ограничен.</b>\n"
                "Команда <code>/check_user</code> доступна только владельцу бота."
            )
            return
        if message.chat.type != "private":
            await message.answer(
                "<b>TelegaGFY</b>\n\n"
                "Команду <code>/check_user</code> нужно запускать только в личном чате с ботом."
            )
            return

        try:
            user_id, chat_id = _parse_check_user_args(command.args if command is not None else None)
        except ValueError as exc:
            await message.answer(str(exc))
            return

        result = await detector.check_user(
            chat_id=chat_id or 0,
            user_id=user_id,
            user_entity=None,
        )
        await message.answer(_render_check_result(user_id=user_id, chat_id=chat_id, result=result))

    @router.message(Command("broadcast"))
    async def handle_broadcast_command(message: Message) -> None:
        if not _is_owner_message(message, owner_user_id):
            await message.answer(
                "<b>Доступ ограничен.</b>\n"
                "Команда <code>/broadcast</code> доступна только владельцу бота."
            )
            return
        if message.chat.type != "private":
            await message.answer(
                "<b>TelegaGFY</b>\n\n"
                "Команду <code>/broadcast</code> нужно запускать только в личном чате с ботом."
            )
            return

        _clear_session(sessions, message.from_user.id)
        sessions[message.from_user.id] = BroadcastSession(status="awaiting_content")
        await message.answer(
            "<b>Рассылка</b>\n"
            "Отправьте следующим сообщением текст, медиа или альбом.\n\n"
            "Я покажу предпросмотр и отдельно попрошу подтвердить рассылку кнопками <b>Да</b> / <b>Нет</b>."
        )

    @router.message(
        lambda message: _is_waiting_for_broadcast(
            message,
            owner_user_id=owner_user_id,
            sessions=sessions,
        )
    )
    async def handle_broadcast_payload(message: Message, bot: Bot) -> None:
        user_id = message.from_user.id
        session = sessions.get(user_id)
        if session is None:
            return

        if message.media_group_id:
            if session.media_group_id not in {None, message.media_group_id}:
                _reset_media_group_state(session)
            session.status = "collecting_album"
            session.media_group_id = message.media_group_id
            if message.message_id not in session.media_group_message_ids:
                session.media_group_message_ids.append(message.message_id)
            if session.finalize_task is not None:
                session.finalize_task.cancel()
            session.finalize_task = asyncio.create_task(
                _finalize_media_group(
                    bot=bot,
                    message=message,
                    recipient_repository=recipient_repository,
                    session=session,
                )
            )
            return

        await _prepare_broadcast_preview(
            bot=bot,
            message=message,
            recipient_repository=recipient_repository,
            session=session,
            message_ids=(message.message_id,),
        )

    @router.callback_query(
        BroadcastCallback.filter(),
        lambda query: _is_owner_private_query(query, owner_user_id),
    )
    async def handle_broadcast_callback(
        query: CallbackQuery,
        callback_data: BroadcastCallback,
        bot: Bot,
    ) -> None:
        user_id = query.from_user.id
        session = sessions.get(user_id)
        draft = None if session is None else session.draft
        if session is None or draft is None or draft.token != callback_data.token:
            await query.answer("Черновик рассылки уже устарел.", show_alert=True)
            return

        if callback_data.action == "cancel":
            _clear_session(sessions, user_id)
            if query.message is not None:
                await query.message.edit_text(
                    "<b>Рассылка отменена.</b>\n"
                    "Черновик удалён, сообщения пользователям не отправлялись."
                )
            await query.answer("Рассылка отменена.")
            return

        recipients = [
            recipient_id
            for recipient_id in await recipient_repository.list_recipient_user_ids()
            if recipient_id != user_id
        ]
        if query.message is not None:
            await query.message.edit_text(
                "<b>Рассылка запущена.</b>\n"
                f"Получателей в очереди: <b>{len(recipients)}</b>."
            )
        await query.answer("Начинаю рассылку.")

        session.status = "sending"
        success_count = 0
        failed_count = 0
        for recipient_id in recipients:
            try:
                await _send_payload(
                    bot=bot,
                    target_chat_id=recipient_id,
                    source_chat_id=draft.source_chat_id,
                    message_ids=draft.message_ids,
                )
            except Exception:
                LOGGER.warning("Broadcast delivery failed for user_id=%s", recipient_id, exc_info=True)
                failed_count += 1
            else:
                success_count += 1

        _clear_session(sessions, user_id)
        if query.message is not None:
            await query.message.edit_text(
                "<b>Рассылка завершена.</b>\n"
                f"Успешно: <b>{success_count}</b>\n"
                f"Ошибки: <b>{failed_count}</b>"
            )

    return router


async def _finalize_media_group(
    *,
    bot: Bot,
    message: Message,
    recipient_repository: PrivateUsersRepository,
    session: BroadcastSession,
) -> None:
    try:
        await asyncio.sleep(_MEDIA_GROUP_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return

    message_ids = tuple(session.media_group_message_ids)
    session.finalize_task = None
    if not message_ids:
        return

    await _prepare_broadcast_preview(
        bot=bot,
        message=message,
        recipient_repository=recipient_repository,
        session=session,
        message_ids=message_ids,
    )


async def _prepare_broadcast_preview(
    *,
    bot: Bot,
    message: Message,
    recipient_repository: PrivateUsersRepository,
    session: BroadcastSession,
    message_ids: tuple[int, ...],
) -> None:
    user_id = message.from_user.id
    _cancel_finalize_task(session)

    token = secrets.token_hex(6)
    draft = BroadcastDraft(
        source_chat_id=message.chat.id,
        message_ids=message_ids,
        token=token,
    )
    preview_message_ids = await _send_payload(
        bot=bot,
        target_chat_id=message.chat.id,
        source_chat_id=message.chat.id,
        message_ids=message_ids,
    )
    recipients = [
        recipient_id
        for recipient_id in await recipient_repository.list_recipient_user_ids()
        if recipient_id != user_id
    ]
    confirmation = await bot.send_message(
        chat_id=message.chat.id,
        text=_render_broadcast_confirmation(len(recipients), len(message_ids) > 1),
        reply_markup=broadcast_confirmation_keyboard(token),
        disable_web_page_preview=True,
    )

    draft.preview_message_ids = tuple(preview_message_ids)
    draft.confirmation_message_id = confirmation.message_id
    session.status = "awaiting_confirmation"
    session.draft = draft
    _reset_media_group_state(session)


async def _send_payload(
    *,
    bot: Bot,
    target_chat_id: int,
    source_chat_id: int,
    message_ids: tuple[int, ...],
) -> list[int]:
    if len(message_ids) == 1:
        return [
            await _copy_or_forward_single_message(
                bot=bot,
                target_chat_id=target_chat_id,
                source_chat_id=source_chat_id,
                message_id=message_ids[0],
            )
        ]

    try:
        result = await bot.copy_messages(
            chat_id=target_chat_id,
            from_chat_id=source_chat_id,
            message_ids=list(message_ids),
        )
        return [item.message_id for item in result]
    except TelegramRetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after))
        return await _send_payload(
            bot=bot,
            target_chat_id=target_chat_id,
            source_chat_id=source_chat_id,
            message_ids=message_ids,
        )
    except TelegramBadRequest:
        copied_ids: list[int] = []
        for message_id in message_ids:
            copied_ids.append(
                await _copy_or_forward_single_message(
                    bot=bot,
                    target_chat_id=target_chat_id,
                    source_chat_id=source_chat_id,
                    message_id=message_id,
                )
            )
        return copied_ids


async def _copy_or_forward_single_message(
    *,
    bot: Bot,
    target_chat_id: int,
    source_chat_id: int,
    message_id: int,
) -> int:
    try:
        result = await bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=source_chat_id,
            message_id=message_id,
        )
        return result.message_id
    except TelegramRetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after))
        return await _copy_or_forward_single_message(
            bot=bot,
            target_chat_id=target_chat_id,
            source_chat_id=source_chat_id,
            message_id=message_id,
        )
    except TelegramBadRequest:
        forwarded = await bot.forward_message(
            chat_id=target_chat_id,
            from_chat_id=source_chat_id,
            message_id=message_id,
        )
        return forwarded.message_id


def _is_owner_message(message: Message, owner_user_id: int | None) -> bool:
    return (
        message.from_user is not None
        and owner_user_id is not None
        and message.from_user.id == owner_user_id
    )


def _is_owner_private_message(message: Message, owner_user_id: int | None) -> bool:
    return _is_owner_message(message, owner_user_id) and message.chat.type == "private"


def _is_waiting_for_broadcast(
    message: Message,
    *,
    owner_user_id: int | None,
    sessions: dict[int, BroadcastSession],
) -> bool:
    if not _is_owner_private_message(message, owner_user_id):
        return False
    session = sessions.get(message.from_user.id)
    return session is not None and session.status in {"awaiting_content", "collecting_album"}


def _is_owner_private_query(query: CallbackQuery, owner_user_id: int | None) -> bool:
    return (
        owner_user_id is not None
        and query.from_user.id == owner_user_id
        and getattr(getattr(query.message, "chat", None), "type", None) == "private"
    )


def _clear_session(sessions: dict[int, BroadcastSession], user_id: int) -> None:
    session = sessions.pop(user_id, None)
    if session is None:
        return
    _cancel_finalize_task(session)


def _cancel_finalize_task(session: BroadcastSession) -> None:
    if session.finalize_task is not None:
        session.finalize_task.cancel()
        session.finalize_task = None


def _reset_media_group_state(session: BroadcastSession) -> None:
    session.media_group_id = None
    session.media_group_message_ids.clear()


def _parse_check_user_args(args: str | None) -> tuple[int, int | None]:
    parts = (args or "").split()
    if len(parts) not in {1, 2}:
        raise ValueError(
            "<b>Формат команды</b>\n"
            "<code>/check_user &lt;user_id&gt; [chat_id]</code>"
        )

    try:
        user_id = int(parts[0])
    except ValueError as exc:
        raise ValueError("<code>user_id</code> должен быть числом.") from exc
    if user_id <= 0:
        raise ValueError("<code>user_id</code> должен быть положительным числом.")

    chat_id: int | None = None
    if len(parts) == 2:
        try:
            chat_id = int(parts[1])
        except ValueError as exc:
            raise ValueError("<code>chat_id</code> должен быть числом.") from exc
        if chat_id == 0:
            raise ValueError("<code>chat_id</code> не может быть <code>0</code>.")

    return user_id, chat_id


def _render_check_result(*, user_id: int, chat_id: int | None, result: TelegaCheckResult) -> str:
    notes = (
        "\n".join(f"• {html.quote(note)}" for note in result.notes)
        if result.notes
        else "<i>нет заметок</i>"
    )
    chat_label = f"<code>{chat_id}</code>" if chat_id is not None else "<i>не указан</i>"
    return (
        "<b>Ручная проверка пользователя</b>\n"
        f"<b>User ID:</b> <code>{user_id}</code>\n"
        f"<b>Chat ID:</b> {chat_label}\n\n"
        "<b>Статус</b>\n"
        f"• Активная сессия сейчас: {_status_label(result.active_now)}\n"
        f"• Использовал Telega раньше: {_status_label(result.used_before)}\n"
        f"• Поддержка <code>active-session</code>: {'да' if result.active_supported else 'нет'}\n\n"
        "<b>Источники</b>\n"
        f"• Active: <code>{html.quote(result.active_source or '-')}</code>\n"
        f"• History: <code>{html.quote(result.used_before_source or '-')}</code>\n"
        f"• Проверено: <code>{result.checked_at.isoformat()}</code>\n\n"
        "<b>Примечания</b>\n"
        f"{notes}\n\n"
        "<i>Подсказка:</i> если <code>active_now</code> неизвестен, повторите проверку с "
        "<code>chat_id</code> группы или канала, где Telethon-аккаунт видит пользователя через MTProto."
    )


def _render_broadcast_confirmation(recipient_count: int, is_album: bool) -> str:
    payload_label = "альбом" if is_album else "сообщение"
    return (
        "<b>Предпросмотр рассылки готов.</b>\n"
        f"Тип: <b>{payload_label}</b>\n"
        f"Получателей: <b>{recipient_count}</b>\n\n"
        "Разослать это пользователям?"
    )


def _status_label(value: bool | None) -> str:
    if value is True:
        return "✅ да"
    if value is False:
        return "❌ нет"
    return "❔ неизвестно"
