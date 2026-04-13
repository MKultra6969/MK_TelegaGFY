from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.filters import CommandStart
from aiogram import html
from aiogram.types import Message

from telega_guard.bot.access import has_private_access
from telega_guard.bot.keyboards import start_keyboard
from telega_guard.repositories.chat_settings import ChatSettingsRepository


def create_start_router(
    repository: ChatSettingsRepository,
    owner_user_id: int | None = None,
) -> Router:
    router = Router(name="start")

    @router.message(CommandStart())
    async def handle_start(message: Message, bot: Bot) -> None:
        if getattr(getattr(message, "chat", None), "type", None) != "private":
            return
        if message.from_user is None:
            return
        if not await has_private_access(
            repository,
            bot,
            message.from_user.id,
            owner_user_id=owner_user_id,
        ):
            return

        me = await bot.get_me()
        await message.answer(
            (
                "<b>TelegaGFY</b>\n"
                "Бот для модерации пользователей, связанных с Telega.\n\n"
                "<b>Как начать</b>\n"
                "• Добавьте бота в группу, супергруппу или канал.\n"
                "• Выдайте право на блокировку пользователей.\n"
                "• После этого вернитесь сюда и откройте <code>/settings</code>.\n"
                "• Настройки всех доступных чатов открываются только из личного чата с ботом.\n\n"
                "<b>Быстрое добавление</b>\n"
                "Используйте кнопки ниже, чтобы сразу открыть выбор группы/беседы или канала.\n\n"
                "<i>Важно:</i> для проверки <code>active-session</code> Telethon-аккаунт должен видеть пользователя через MTProto."
            ),
            reply_markup=start_keyboard(me.username),
            disable_web_page_preview=True,
        )

    @router.message(Command("info"))
    async def handle_info(message: Message, bot: Bot) -> None:
        me = await bot.get_me()
        username = (
            f"@{html.quote(me.username)}"
            if getattr(me, "username", None)
            else "<i>не задан</i>"
        )
        await message.answer(
            (
                "<b>TelegaGFY</b>\n"
                "<i>Информация о боте и полезные ссылки</i>\n\n"
                "<b>Профиль</b>\n"
                f"• <b>Bot:</b> {username}\n"
                "• <b>Назначение:</b> модерация пользователей, связанных с Telega\n\n"
                "<b>Автор</b>\n"
                "• <a href=\"https://t.me/mkultra6969\">@mkultra6969</a>\n"
                "• <a href=\"https://t.me/MKplusULTRA\">Канал MKplusULTRA</a>\n\n"
                "<b>Проект</b>\n"
                "• <a href=\"https://github.com/MKultra6969/MK_TelegaGFY\">GitHub: MK_TelegaGFY</a>\n\n"
                "<b>Благодарность</b>\n"
                "• Референс: <a href=\"https://t.me/kvuco\">@kvuco</a>\n"
                "• <a href=\"https://t.me/kvucoPlugins\">Канал kvucoPlugins</a>"
            ),
            disable_web_page_preview=True,
        )

    return router
