from __future__ import annotations

import asyncio
import getpass
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

from telega_guard.config import Settings
from telega_guard.logging import configure_logging


def _password_provider(explicit_password: str | None):
    if explicit_password:
        return explicit_password
    if sys.stdin.isatty():
        return lambda: getpass.getpass("Please enter your Telegram 2FA password: ")
    return explicit_password


async def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    settings.telethon_session_file.parent.mkdir(parents=True, exist_ok=True)

    if settings.telethon_session_string:
        client = TelegramClient(
            StringSession(settings.telethon_session_string),
            settings.api_id,
            settings.api_hash,
        )
    else:
        client = TelegramClient(
            str(settings.telethon_session_file),
            settings.api_id,
            settings.api_hash,
        )

    try:
        await client.start(
            phone=settings.telethon_phone,
            password=_password_provider(settings.telethon_2fa_password),
        )
        me = await client.get_me()
        print(f"Telethon session is ready for user_id={getattr(me, 'id', 'unknown')}")
        if settings.telethon_session_string:
            print("TELETHON_SESSION_STRING mode is active.")
        else:
            print(f"Session file saved near: {settings.telethon_file_session_path}")
    finally:
        await client.disconnect()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
