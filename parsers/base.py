"""Abstract parser strategy.

Each marketplace ships a concrete subclass of :class:`BaseParser`. The dispatcher picks a
strategy by URL, so adding a marketplace means adding one module and one registry entry —
nothing in the bot layer changes.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from types import TracebackType
from typing import ClassVar, Self

import httpx

from config import Settings
from parsers.exceptions import UnsupportedURLError
from parsers.http import BrowserSession, create_http_client
from parsers.utils import host_of
from schemas.product import Marketplace, ProductInfo


class BaseParser(ABC):
    """Strategy interface for a single marketplace.

    Subclasses must declare :attr:`marketplace` and :attr:`domains`, and implement
    :meth:`parse`. HTTP clients are created lazily and shared for the parser's lifetime;
    :meth:`aclose` releases them.
    """

    #: Marketplace this strategy speaks for.
    marketplace: ClassVar[Marketplace]
    #: Hostnames (without ``www.``) routed to this strategy; suffix match is used.
    domains: ClassVar[tuple[str, ...]] = ()
    #: Optional extra check on the path, e.g. "must look like a product page".
    path_pattern: ClassVar[re.Pattern[str] | None] = None

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http: httpx.AsyncClient | None = None
        self._browser: BrowserSession | None = None

    # ------------------------------------------------------------------ routing

    @classmethod
    def can_handle(cls, url: str) -> bool:
        """Return ``True`` when this strategy recognises *url*."""
        host = host_of(url)
        if not any(host == domain or host.endswith(f".{domain}") for domain in cls.domains):
            return False
        if cls.path_pattern is not None:
            return cls.path_pattern.search(url) is not None
        return True

    # ------------------------------------------------------------------ clients

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def http(self) -> httpx.AsyncClient:
        """Plain HTTP client for JSON APIs that do not fingerprint clients."""
        if self._http is None:
            self._http = create_http_client(
                self._settings.request_timeout, self._settings.parser_proxy
            )
        return self._http

    @property
    def browser(self) -> BrowserSession:
        """TLS-impersonating client for pages behind anti-bot protection."""
        if self._browser is None:
            self._browser = BrowserSession(
                impersonate=self._settings.impersonate,
                timeout=self._settings.request_timeout,
                proxy=self._settings.parser_proxy,
            )
        return self._browser

    # ------------------------------------------------------------------ contract

    @abstractmethod
    async def parse(self, url: str) -> ProductInfo:
        """Fetch *url* and return a normalised product card.

        Raises:
            ParserError: any subclass of it, always with a user-facing ``user_message``.
        """

    def ensure_supported(self, url: str) -> None:
        """Guard used by :meth:`parse` implementations before doing any I/O."""
        if not self.can_handle(url):
            raise UnsupportedURLError(f"{type(self).__name__} cannot handle {url}")

    # ------------------------------------------------------------------ lifecycle

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        if self._browser is not None:
            await self._browser.aclose()
            self._browser = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} marketplace={self.marketplace.value!r}>"
