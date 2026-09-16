"""Application settings loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Runtime configuration.

    Field names map to upper-case environment variables (``bot_token`` -> ``BOT_TOKEN``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    bot_token: SecretStr
    telegram_proxy: str | None = Field(
        default=None,
        description=(
            "Proxy for Telegram API calls only, e.g. socks5://user:pass@host:1080 or "
            "http://host:3128. Marketplace requests always go out directly, so the bot can "
            "keep a Russian IP for parsing while reaching Telegram through a proxy."
        ),
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Cache
    cache_ttl: int = Field(default=3600, ge=60, description="Product card TTL, seconds")
    cache_prefix: str = "product"

    # Networking
    request_timeout: float = Field(default=15.0, gt=0)
    impersonate: str = Field(
        default="chrome124",
        description="curl_cffi browser profile used for TLS fingerprint impersonation",
    )
    max_image_bytes: int = Field(default=10 * 1024 * 1024, gt=0)

    # Anti-flood
    throttle_rate: float = Field(default=1.5, ge=0)

    # Misc
    log_level: LogLevel = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    return Settings()
