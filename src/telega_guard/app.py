from __future__ import annotations

import getpass
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from telethon import TelegramClient
from telethon.sessions import StringSession

from telega_guard.bot.handlers.admin import create_admin_router
from telega_guard.bot.handlers.membership import create_membership_router
from telega_guard.bot.handlers.owner import create_owner_router
from telega_guard.bot.handlers.start import create_start_router
from telega_guard.bot.middlewares import InteractionLoggingMiddleware
from telega_guard.config import Settings
from telega_guard.db import Database
from telega_guard.repositories.chat_settings import ChatSettingsRepository
from telega_guard.repositories.lookup_cache import LookupCacheRepository
from telega_guard.repositories.moderation_events import ModerationEventRepository
from telega_guard.repositories.runtime_state import RuntimeStateRepository
from telega_guard.services.channel_admin_log import ChannelAdminLogPoller
from telega_guard.services.detector import TelethonDetectorService
from telega_guard.services.lookup import CallsLookupService
from telega_guard.services.moderation import ModerationCoordinator
from telega_guard.userbot.watchers import TelethonWatcher

LOGGER = logging.getLogger(__name__)


def _password_provider(explicit_password: str | None):
    if explicit_password:
        return explicit_password
    if sys.stdin.isatty():
        return lambda: getpass.getpass("Please enter your Telegram 2FA password: ")
    return explicit_password


class TelegaGuardApplication:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(str(settings.database_file))
        self.bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dispatcher = Dispatcher()
        self._interaction_logger = InteractionLoggingMiddleware()
        self.telethon = self._build_telethon_client()

        self.chat_settings_repository = ChatSettingsRepository(self.db)
        self.lookup_repository = LookupCacheRepository(self.db)
        self.runtime_repository = RuntimeStateRepository(self.db)
        self.event_repository = ModerationEventRepository(self.db)

        self.lookup_service = CallsLookupService(
            self.lookup_repository,
            cache_ttl_seconds=settings.lookup_cache_ttl_seconds,
            failure_cooldown_seconds=settings.lookup_failure_cooldown_seconds,
        )
        self.detector = TelethonDetectorService(self.telethon, self.lookup_service)
        self.moderation = ModerationCoordinator(
            bot=self.bot,
            settings_repository=self.chat_settings_repository,
            event_repository=self.event_repository,
            detector=self.detector,
            duplicate_ttl_seconds=settings.duplicate_join_ttl_seconds,
        )
        self.telethon_watcher = TelethonWatcher(
            repository=self.chat_settings_repository,
            detector=self.detector,
            moderation=self.moderation,
        )
        self.channel_poller = ChannelAdminLogPoller(
            client=self.telethon,
            settings_repository=self.chat_settings_repository,
            runtime_repository=self.runtime_repository,
            detector=self.detector,
            moderation=self.moderation,
            poll_interval_seconds=settings.channel_admin_log_poll_seconds,
        )

    async def run(self) -> None:
        await self._startup()
        try:
            await self.dispatcher.start_polling(
                self.bot,
                allowed_updates=self.dispatcher.resolve_used_update_types(),
            )
        finally:
            await self._shutdown()

    async def _startup(self) -> None:
        self.settings.database_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings.telethon_session_file.parent.mkdir(parents=True, exist_ok=True)

        await self.db.connect()
        await self.db.init_schema()
        await self.lookup_service.start()
        await self.moderation.start()

        LOGGER.info("Starting Telethon userbot session")
        await self.telethon.connect()
        if not await self.telethon.is_user_authorized() and not sys.stdin.isatty():
            await self.telethon.disconnect()
            raise RuntimeError(
                "Telethon session is not authorized. Run `docker compose run --rm auth` "
                "first or provide TELETHON_SESSION_STRING."
            )
        await self.telethon.start(
            phone=self.settings.telethon_phone,
            password=_password_provider(self.settings.telethon_2fa_password),
        )
        self.telethon_watcher.install(self.telethon)
        await self.channel_poller.start()

        self.dispatcher.message.outer_middleware(self._interaction_logger)
        self.dispatcher.callback_query.outer_middleware(self._interaction_logger)
        self.dispatcher.chat_member.outer_middleware(self._interaction_logger)
        self.dispatcher.my_chat_member.outer_middleware(self._interaction_logger)

        self.dispatcher.include_router(
            create_start_router(
                self.chat_settings_repository,
                owner_user_id=self.settings.owner_user_id,
            )
        )
        self.dispatcher.include_router(
            create_owner_router(self.detector, self.settings.owner_user_id)
        )
        self.dispatcher.include_router(
            create_admin_router(
                self.chat_settings_repository,
                self.event_repository,
                owner_user_id=self.settings.owner_user_id,
            )
        )
        self.dispatcher.include_router(
            create_membership_router(self.chat_settings_repository, self.moderation)
        )
        await self._setup_bot_commands()
        await self.bot.delete_webhook(drop_pending_updates=False)
        LOGGER.info("TelegaGFY is ready")

    async def _shutdown(self) -> None:
        LOGGER.info("Shutting down TelegaGFY")
        self.telethon_watcher.uninstall(self.telethon)
        await self.channel_poller.close()
        await self.moderation.close()
        await self.lookup_service.close()
        await self.telethon.disconnect()
        await self.bot.session.close()
        await self.db.close()

    def _build_telethon_client(self) -> TelegramClient:
        if self.settings.telethon_session_string:
            return TelegramClient(
                StringSession(self.settings.telethon_session_string),
                self.settings.api_id,
                self.settings.api_hash,
            )
        return TelegramClient(
            str(self.settings.telethon_session_file),
            self.settings.api_id,
            self.settings.api_hash,
        )

    async def _setup_bot_commands(self) -> None:
        await self.bot.set_my_commands(
            [
                BotCommand(command="start", description="Открыть стартовое сообщение"),
                BotCommand(command="settings", description="Настройки ваших чатов"),
                BotCommand(command="logs", description="Посмотреть логи модерации"),
                BotCommand(command="info", description="Информация о боте"),
            ]
        )
