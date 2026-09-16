"""Application settings loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Final, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

#: Proxy schemes understood by aiohttp_socks. Note: "socks5h" and "https" are NOT among them.
_PROXY_SCHEMES: Final[frozenset[str]] = frozenset({"socks4", "socks5", "http"})


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

    @field_validator("telegram_proxy", "parser_proxy", mode="before")
    @classmethod
    def _validate_proxy(cls, value: object, info: ValidationInfo) -> str | None:
        """Reject proxy URLs the underlying clients cannot parse.

        Checked here rather than at connection time: an unusable value otherwise blows up
        deep inside the HTTP client with an opaque ``Invalid scheme component`` and takes
        the whole process down before polling even starts.
        """
        if not isinstance(value, str) or not value.strip():
            return None

        name = (info.field_name or "proxy").upper()
        url = value.strip()
        parts = urlsplit(url)
        if parts.scheme not in _PROXY_SCHEMES:
            raise ValueError(
                f"{name} has an unsupported scheme {parts.scheme!r}. "
                f"Use one of: {', '.join(f'{s}://host:port' for s in sorted(_PROXY_SCHEMES))}"
            )
        try:
            port = parts.port
        except ValueError as exc:  # non-numeric or out-of-range port
            raise ValueError(f"{name} has an invalid port: {exc}") from exc
        if port is None:
            raise ValueError(
                f"{name} must include an explicit port, e.g. "
                f"{parts.scheme}://{parts.hostname or 'host'}:1080"
            )
        return url

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
    parser_proxy: str | None = Field(
        default=None,
        description=(
            "Proxy for marketplace requests only, independent of TELEGRAM_PROXY. Needed when "
            "the server IP is blocked by the marketplace WAF (datacenter ranges usually are)."
        ),
    )

    # Anti-flood
    throttle_rate: float = Field(default=1.5, ge=0)

    # Misc
    log_level: LogLevel = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    return Settings()
