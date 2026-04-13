from __future__ import annotations

import logging


LOG_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)-28s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str) -> None:
    root_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=root_level,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        force=True,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
