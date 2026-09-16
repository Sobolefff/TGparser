"""URL router that picks the right parser strategy.

The dispatcher owns the parser instances (and therefore their HTTP sessions) for the whole
application lifetime, so connections are reused between updates.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from types import TracebackType
from typing import Self

from config import Settings
from parsers.base import BaseParser
from parsers.exceptions import ParserError, UnsupportedURLError
from parsers.ozon import OzonParser
from parsers.utils import clean_url
from parsers.wb import WildberriesParser
from parsers.yandex import YandexMarketParser
from schemas.product import Marketplace, ProductInfo

logger = logging.getLogger(__name__)

#: Registration order matters only for overlapping domains; there are none today.
PARSER_CLASSES: tuple[type[BaseParser], ...] = (
    WildberriesParser,
    OzonParser,
    YandexMarketParser,
)


class ParserDispatcher:
    """Strategy selector: maps a URL to the parser that can handle it."""

    def __init__(self, parsers: Sequence[BaseParser]) -> None:
        self._parsers = tuple(parsers)

    @classmethod
    def from_settings(
        cls, settings: Settings, parser_classes: Iterable[type[BaseParser]] = PARSER_CLASSES
    ) -> Self:
        return cls([parser_class(settings) for parser_class in parser_classes])

    # ------------------------------------------------------------------ routing

    def resolve(self, url: str) -> BaseParser:
        """Return the strategy for *url*.

        Raises:
            UnsupportedURLError: no registered parser recognises the URL.
        """
        for parser in self._parsers:
            if parser.can_handle(url):
                return parser
        raise UnsupportedURLError(f"no parser registered for {url}")

    def supports(self, url: str) -> bool:
        return any(parser.can_handle(url) for parser in self._parsers)

    @property
    def marketplaces(self) -> tuple[Marketplace, ...]:
        return tuple(parser.marketplace for parser in self._parsers)

    # ------------------------------------------------------------------ work

    async def parse(self, url: str) -> ProductInfo:
        """Normalise *url*, route it to a strategy and return the product card."""
        target = clean_url(url)
        parser = self.resolve(target)
        logger.debug("routing %s to %s", target, type(parser).__name__)
        try:
            return await parser.parse(target)
        except ParserError:
            raise
        except Exception as exc:  # defensive: a broken strategy must not kill the bot
            logger.exception("unexpected failure in %s for %s", type(parser).__name__, target)
            raise ParserError(f"unexpected error in {type(parser).__name__}: {exc}") from exc

    # ------------------------------------------------------------------ lifecycle

    async def aclose(self) -> None:
        for parser in self._parsers:
            await parser.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
