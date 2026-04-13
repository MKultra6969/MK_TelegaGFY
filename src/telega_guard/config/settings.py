from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(alias="BOT_TOKEN")
    admin_user_id: int | None = Field(default=None, alias="ADMIN_USER_ID")
    api_id: int = Field(alias="API_ID")
    api_hash: str = Field(alias="API_HASH")
    telethon_phone: str = Field(alias="TELETHON_PHONE")
    telethon_session: str = Field(default="sessions/telega_guard", alias="TELETHON_SESSION")
    telethon_session_string: str | None = Field(
        default=None,
        alias="TELETHON_SESSION_STRING",
    )
    telethon_2fa_password: str | None = Field(
        default=None,
        alias="TELETHON_2FA_PASSWORD",
    )
    owner_user_id: int | None = Field(default=None, alias="OWNER_USER_ID")
    database_path: str = Field(default="data/telega_guard.sqlite3", alias="DATABASE_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    channel_admin_log_poll_seconds: int = Field(default=30, alias="CHANNEL_ADMIN_LOG_POLL_SECONDS")
    lookup_cache_ttl_seconds: int = Field(default=21600, alias="LOOKUP_CACHE_TTL_SECONDS")
    lookup_failure_cooldown_seconds: int = Field(
        default=1800,
        alias="LOOKUP_FAILURE_COOLDOWN_SECONDS",
    )
    duplicate_join_ttl_seconds: int = Field(default=90, alias="DUPLICATE_JOIN_TTL_SECONDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def database_file(self) -> Path:
        return Path(self.database_path).expanduser()

    @property
    def telethon_session_file(self) -> Path:
        return Path(self.telethon_session).expanduser()

    @property
    def telethon_file_session_path(self) -> Path:
        base = self.telethon_session_file
        if base.suffix == ".session":
            return base
        return base.with_suffix(".session")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
