from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from telega_guard.bot.callbacks import BroadcastCallback, LogsCallback, SettingsCallback
from telega_guard.models import ChatSettings


def settings_keyboard(settings: ChatSettings) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"{_mark(settings.ban_if_active_now)} Банить за активную Telega-сессию",
        callback_data=SettingsCallback(
            action="toggle",
            chat_id=settings.chat_id,
            flag="ban_if_active_now",
        ),
    )
    builder.button(
        text=f"{_mark(settings.ban_if_used_before)} Банить за прошлое использование",
        callback_data=SettingsCallback(
            action="toggle",
            chat_id=settings.chat_id,
            flag="ban_if_used_before",
        ),
    )
    builder.button(
        text=f"{_mark(settings.notify_admin_on_detection)} Уведомлять владельца без бана",
        callback_data=SettingsCallback(
            action="toggle",
            chat_id=settings.chat_id,
            flag="notify_admin_on_detection",
        ),
    )
    builder.button(
        text="Обновить",
        callback_data=SettingsCallback(action="refresh", chat_id=settings.chat_id),
    )
    builder.button(
        text="Назад к списку чатов",
        callback_data=SettingsCallback(action="list"),
    )
    builder.adjust(1)
    return builder.as_markup()


def chat_picker_keyboard(chats: Sequence[ChatSettings]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for settings in chats:
        builder.button(
            text=_chat_button_text(settings),
            callback_data=SettingsCallback(action="open", chat_id=settings.chat_id),
        )
    builder.button(
        text="Обновить список",
        callback_data=SettingsCallback(action="list"),
    )
    builder.adjust(1)
    return builder.as_markup()


def logs_chat_picker_keyboard(chats: Sequence[ChatSettings]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for settings in chats:
        builder.button(
            text=_chat_button_text(settings),
            callback_data=LogsCallback(action="open", chat_id=settings.chat_id),
        )
    builder.button(
        text="Обновить список",
        callback_data=LogsCallback(action="list"),
    )
    builder.adjust(1)
    return builder.as_markup()


def logs_view_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Обновить логи",
        callback_data=LogsCallback(action="refresh", chat_id=chat_id),
    )
    builder.button(
        text="Назад к чатам",
        callback_data=LogsCallback(action="list"),
    )
    builder.adjust(1)
    return builder.as_markup()


def start_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    admin_permissions = "restrict_members"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Добавить в группу/беседу",
                    url=f"https://t.me/{bot_username}?startgroup&admin={admin_permissions}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Добавить в канал",
                    url=f"https://t.me/{bot_username}?startchannel&admin={admin_permissions}",
                )
            ],
        ]
    )


def broadcast_confirmation_keyboard(token: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Да",
        callback_data=BroadcastCallback(action="send", token=token),
    )
    builder.button(
        text="Нет",
        callback_data=BroadcastCallback(action="cancel", token=token),
    )
    builder.adjust(2)
    return builder.as_markup()


def _mark(value: bool) -> str:
    return "✅" if value else "☑️"


def _chat_button_text(settings: ChatSettings) -> str:
    chat_kind = {
        "channel": "Канал",
        "supergroup": "Супергруппа",
        "group": "Группа",
    }.get(settings.chat_type, "Чат")
    return f"{chat_kind}: {settings.title or settings.chat_id}"
