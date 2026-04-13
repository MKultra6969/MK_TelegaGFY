from __future__ import annotations

from aiogram import Router, html
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message

from telega_guard.models import TelegaCheckResult
from telega_guard.services.detector import TelethonDetectorService


def create_owner_router(
    detector: TelethonDetectorService,
    owner_user_id: int | None,
) -> Router:
    router = Router(name="owner")

    @router.message(Command("check_user"))
    async def handle_check_user(message: Message, command: CommandObject | None = None) -> None:
        if message.from_user is None or owner_user_id is None or message.from_user.id != owner_user_id:
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

    return router


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


def _status_label(value: bool | None) -> str:
    if value is True:
        return "✅ да"
    if value is False:
        return "❌ нет"
    return "❔ неизвестно"
