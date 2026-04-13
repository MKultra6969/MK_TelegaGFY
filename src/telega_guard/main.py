from __future__ import annotations

import asyncio

from telega_guard.app import TelegaGuardApplication
from telega_guard.config import Settings
from telega_guard.logging import configure_logging


async def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    app = TelegaGuardApplication(settings)
    await app.run()


def run() -> None:
    asyncio.run(main())
