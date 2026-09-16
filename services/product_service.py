"""Application service that ties caching, parsing and image fetching together.

This is the single entry point the bot layer talks to; handlers never touch Redis or a
parser directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import httpx

from config import Settings
from parsers.dispatcher import ParserDispatcher
from parsers.http import create_http_client
from schemas.product import ProductInfo
from services.cache import ProductCache
from services.images import ImageFetcher, ImagePayload

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProductCard:
    """A parsed product plus everything needed to render it in Telegram."""

    product: ProductInfo
    image: ImagePayload | None
    from_cache: bool


class ProductService:
    """Cache-aside facade over :class:`ParserDispatcher`."""

    def __init__(
        self,
        *,
        dispatcher: ParserDispatcher,
        cache: ProductCache,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._cache = cache
        self._settings = settings
        self._http = http_client or create_http_client(
            settings.request_timeout, settings.parser_proxy
        )
        self._owns_http = http_client is None
        self._images = ImageFetcher(self._http, max_bytes=settings.max_image_bytes)

    @property
    def dispatcher(self) -> ParserDispatcher:
        return self._dispatcher

    def supports(self, url: str) -> bool:
        return self._dispatcher.supports(url)

    async def get_card(self, url: str, *, with_image: bool = True) -> ProductCard:
        """Return the card for *url*, parsing it only when the cache misses.

        Raises:
            ParserError: propagated from the strategy; always carries ``user_message``.
        """
        product = await self._cache.get(url)
        from_cache = product is not None

        if product is None:
            product = await self._dispatcher.parse(url)
            await self._cache.set_alias(url, product)
            logger.info(
                "parsed %s from %s (cache miss)", product.product_id, product.marketplace_name
            )

        image: ImagePayload | None = None
        if with_image and product.image_url is not None:
            image = await self._images.fetch(
                str(product.image_url), filename=product.product_id or "product"
            )

        return ProductCard(product=product, image=image, from_cache=from_cache)

    async def refresh(self, url: str) -> ProductCard:
        """Force a re-parse, dropping any cached copy first."""
        await self._cache.invalidate(url)
        return await self.get_card(url)

    async def aclose(self) -> None:
        await self._dispatcher.aclose()
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
