from __future__ import annotations

import logging

from aiogram import Bot, Router, html
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Chat, ChatMemberUpdated, Message

from telega_guard.bot.access import administered_chats, is_chat_owner, owned_chats
from telega_guard.bot.callbacks import LogsCallback, SettingsCallback
from telega_guard.bot.keyboards import (
    chat_picker_keyboard,
    logs_chat_picker_keyboard,
    logs_view_keyboard,
    settings_keyboard,
)
from telega_guard.models import ChatSettings
from telega_guard.repositories.chat_settings import ChatSettingsRepository
from telega_guard.repositories.moderation_events import ModerationEvent, ModerationEventRepository

LOGGER = logging.getLogger(__name__)


def create_admin_router(
    repository: ChatSettingsRepository,
    event_repository: ModerationEventRepository,
    owner_user_id: int | None = None,
) -> Router:
    router = Router(name="admin")

    @router.message(Command("settings"))
    async def handle_settings_command(message: Message, bot: Bot) -> None:
        if message.from_user is None:
            return

        if message.chat.type != "private":
            return

        user_id = message.from_user.id
        chats = await owned_chats(repository, bot, user_id)
        if not chats and owner_user_id != user_id:
            return

        if not chats:
            await message.answer(render_empty_chat_list_text(), disable_web_page_preview=True)
            return

        await message.answer(
            render_chat_list_text(chats),
            reply_markup=chat_picker_keyboard(chats),
            disable_web_page_preview=True,
        )

    @router.message(Command("logs"))
    async def handle_logs_command(message: Message, bot: Bot) -> None:
        if message.from_user is None:
            return

        if message.chat.type != "private":
            return

        chats = await administered_chats(repository, bot, message.from_user.id)
        if not chats:
            await message.answer(render_empty_logs_text(), disable_web_page_preview=True)
            return

        await message.answer(
            render_logs_chat_list_text(chats),
            reply_markup=logs_chat_picker_keyboard(chats),
            disable_web_page_preview=True,
        )

    @router.callback_query(SettingsCallback.filter())
    async def handle_settings_callback(
        query: CallbackQuery,
        callback_data: SettingsCallback,
        bot: Bot,
    ) -> None:
        if query.from_user is None:
            await query.answer()
            return

        if callback_data.action == "list":
            user_id = query.from_user.id
            chats = await owned_chats(repository, bot, user_id)
            if not chats and owner_user_id != user_id:
                await query.answer()
                return

            if query.message is not None:
                if chats:
                    await _edit_message(
                        query.message,
                        render_chat_list_text(chats),
                        reply_markup=chat_picker_keyboard(chats),
                    )
                else:
                    await _edit_message(query.message, render_empty_chat_list_text())
            await query.answer("Список чатов обновлён.")
            return

        if not await is_chat_owner(bot, callback_data.chat_id, query.from_user.id):
            await query.answer("Настройки может менять только Owner этого чата.", show_alert=True)
            return

        settings = await repository.get(callback_data.chat_id)
        if settings is None:
            await query.answer("Бот ещё не зарегистрировал этот чат. Добавьте его заново.", show_alert=True)
            return

        if callback_data.action == "open":
            if query.message is not None:
                await _edit_settings_message(query.message, settings)
            await query.answer("Панель настроек открыта.")
            return

        if callback_data.action == "toggle":
            if callback_data.flag not in {
                "ban_if_active_now",
                "ban_if_used_before",
                "notify_admin_on_detection",
            }:
                await query.answer("Неизвестная настройка в панели.", show_alert=True)
                return
            value = not bool(getattr(settings, callback_data.flag))
            settings = await repository.set_flag(callback_data.chat_id, callback_data.flag, value)
            if settings is None:
                await query.answer("Не удалось обновить настройки. Попробуйте ещё раз.", show_alert=True)
                return

        if query.message is not None:
            await _edit_settings_message(query.message, settings)
        await query.answer("Настройки обновлены.")

    @router.callback_query(LogsCallback.filter())
    async def handle_logs_callback(
        query: CallbackQuery,
        callback_data: LogsCallback,
        bot: Bot,
    ) -> None:
        if query.from_user is None:
            await query.answer()
            return

        if callback_data.action == "list":
            chats = await administered_chats(repository, bot, query.from_user.id)
            if query.message is not None:
                if chats:
                    await _edit_message(
                        query.message,
                        render_logs_chat_list_text(chats),
                        reply_markup=logs_chat_picker_keyboard(chats),
                    )
                else:
                    await _edit_message(query.message, render_empty_logs_text())
            await query.answer("Список логов обновлён.")
            return

        chats = await administered_chats(repository, bot, query.from_user.id)
        accessible_ids = {settings.chat_id for settings in chats}
        if callback_data.chat_id not in accessible_ids:
            await query.answer("Логи доступны только администраторам этого чата.", show_alert=True)
            return

        settings = await repository.get(callback_data.chat_id)
        if settings is None:
            await query.answer("Чат ещё не зарегистрирован ботом.", show_alert=True)
            return

        events = await event_repository.list_events(callback_data.chat_id, limit=10)
        if query.message is not None:
            await _edit_message(
                query.message,
                render_logs_text(settings, events),
                reply_markup=logs_view_keyboard(settings.chat_id),
            )
        await query.answer("Логи обновлены.")

    @router.my_chat_member()
    async def handle_my_chat_member(update: ChatMemberUpdated, bot: Bot) -> None:
        chat = update.chat
        if chat.type == "private":
            return

        new_status = str(update.new_chat_member.status)
        old_status = str(update.old_chat_member.status)
        if new_status not in {"member", "administrator"} or old_status == new_status:
            return

        await repository.upsert_chat(chat.id, chat.type, _chat_title(chat))

    return router


def render_settings_text(settings: ChatSettings) -> str:
    return (
        "<b>TelegaGFY</b>\n"
        f"<b>Чат:</b> <code>{html.quote(settings.title or str(settings.chat_id))}</code>\n"
        f"<b>ID:</b> <code>{settings.chat_id}</code>\n"
        f"<b>Тип:</b> <code>{html.quote(settings.chat_type)}</code>\n\n"
        "<b>Правила блокировки</b>\n"
        f"• {_mark(settings.ban_if_active_now)} <b>Активная сессия Telega</b> - банить, если она сейчас активна\n"
        f"• {_mark(settings.ban_if_used_before)} <b>Использовал Telega раньше</b> - банить, если пользователь уже был в Telega\n\n"
        "<b>Уведомления</b>\n"
        f"• {_mark(settings.notify_admin_on_detection)} <b>Уведомление владельцу</b> - отправить в личный чат список найденных Telega-статусов без автобана\n\n"
        "<i>Важно:</i> для проверки <code>active-session</code> Telethon-аккаунт должен видеть пользователя через MTProto."
    )


async def _edit_settings_message(message: Message, settings: ChatSettings) -> None:
    await _edit_message(
        message,
        render_settings_text(settings),
        reply_markup=settings_keyboard(settings),
    )


async def _edit_message(
    message: Message,
    text: str,
    *,
    reply_markup=None,
) -> None:
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def render_chat_list_text(chats: list[ChatSettings]) -> str:
    lines = [
        "<b>TelegaGFY</b>",
        "",
        "<b>Доступные чаты</b>",
        "Ниже показаны только те чаты и каналы, где у вас статус <b>Owner</b>.",
        "",
    ]
    for settings in chats:
        lines.append(
            f"• <b>{html.quote(settings.title or str(settings.chat_id))}</b> "
            f"(<code>{html.quote(settings.chat_type)}</code>)"
        )
    lines.extend(
        [
            "",
            "<i>Важно:</i> бот должен уже быть добавлен в чат, а Telethon-аккаунт должен видеть пользователей через MTProto для проверки <code>active-session</code>.",
        ]
    )
    return "\n".join(lines)


def render_empty_chat_list_text() -> str:
    return (
        "<b>TelegaGFY</b>\n\n"
        "Я не нашёл ни одного чата, где бот уже добавлен и у вас есть статус <b>Owner</b>.\n\n"
        "<b>Что нужно сделать</b>\n"
        "• Добавьте бота в нужный чат, группу или канал.\n"
        "• Выдайте боту право на блокировку пользователей.\n"
        "• Убедитесь, что вы владелец этого чата.\n"
        "• Затем снова откройте здесь <code>/settings</code>."
    )


def render_logs_chat_list_text(chats: list[ChatSettings]) -> str:
    lines = [
        "<b>Логи TelegaGFY</b>",
        "",
        "Выберите чат, канал или беседу, чтобы посмотреть последние события модерации.",
        "",
    ]
    for settings in chats:
        lines.append(
            f"• <b>{html.quote(settings.title or str(settings.chat_id))}</b> "
            f"(<code>{html.quote(settings.chat_type)}</code>)"
        )
    return "\n".join(lines)


def render_empty_logs_text() -> str:
    return (
        "<b>Логи TelegaGFY</b>\n\n"
        "Я не нашёл ни одного доступного чата, где вы сейчас администратор или владелец.\n\n"
        "Когда бот зарегистрирует чат и у вас будут права администратора, здесь появится просмотр логов."
    )


def render_logs_text(settings: ChatSettings, events: list[ModerationEvent]) -> str:
    lines = [
        "<b>Логи TelegaGFY</b>",
        f"<b>Чат:</b> <code>{html.quote(settings.title or str(settings.chat_id))}</code>",
        f"<b>ID:</b> <code>{settings.chat_id}</code>",
        "",
    ]
    if not events:
        lines.append("<i>Событий пока нет.</i>")
        return "\n".join(lines)

    lines.append("<b>Последние события</b>")
    for event in events:
        lines.append(_render_event_line(event))
    return "\n".join(lines)


def _chat_title(chat: Chat) -> str:
    return str(chat.title or getattr(chat, "full_name", None) or chat.id)


def _mark(value: bool) -> str:
    return "✅" if value else "⬜"


def _render_event_line(event: ModerationEvent) -> str:
    title = {
        "ban": "Блокировка",
        "notify": "Личное уведомление",
        "chat_notice": "Уведомление в чат",
    }.get(event.action, event.action)
    reason = {
        "active_telega_session": "активная сессия Telega",
        "used_telega_before": "использовал Telega раньше",
        "multiple_telega_signals": "сразу несколько Telega-статусов",
        "telega_detected": "обнаружен Telega-статус",
    }.get(event.reason, event.reason)
    status_bits: list[str] = []
    if event.details.get("active_now") is True:
        status_bits.append("активная сессия")
    if event.details.get("used_before") is True:
        status_bits.append("использовал раньше")
    suffix = f" | статусы: {', '.join(status_bits)}" if status_bits else ""
    error = event.details.get("error")
    error_suffix = f" | ошибка: {html.quote(str(error))}" if error else ""
    return (
        f"• <code>{html.quote(event.created_at)}</code> | <b>{html.quote(title)}</b> "
        f"| user <code>{event.user_id}</code> | {html.quote(reason)} "
        f"| результат: <code>{html.quote(event.result)}</code>{suffix}{error_suffix}"
    )
