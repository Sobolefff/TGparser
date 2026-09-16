"""Marketplace parser strategies."""

from parsers.base import BaseParser
from parsers.dispatcher import PARSER_CLASSES, ParserDispatcher
from parsers.exceptions import (
    MarketplaceBlockedError,
    ParserError,
    ParserResponseError,
    ParserTimeoutError,
    ProductNotFoundError,
    UnsupportedURLError,
)
from parsers.ozon import OzonParser
from parsers.wb import WildberriesParser
from parsers.yandex import YandexMarketParser

__all__ = [
    "PARSER_CLASSES",
    "BaseParser",
    "MarketplaceBlockedError",
    "OzonParser",
    "ParserDispatcher",
    "ParserError",
    "ParserResponseError",
    "ParserTimeoutError",
    "ProductNotFoundError",
    "UnsupportedURLError",
    "WildberriesParser",
    "YandexMarketParser",
]
