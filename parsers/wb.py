"""Wildberries strategy.

Wildberries exposes an unauthenticated card API (``card.wb.ru``) that returns the whole
product payload as JSON, so no HTML parsing is needed — but it is fronted by a WAF that
rejects clients whose TLS fingerprint is not a browser, hence curl_cffi. Images live on
sharded ``basket-NN`` CDN hosts whose number is derived arithmetically from the article id.
"""

from __future__ import annotations

import asyncio
import json
import re
from decimal import Decimal
from typing import Any, ClassVar, Final
from urllib.parse import urlencode

import httpx

from parsers.base import BaseParser
from parsers.exceptions import ParserResponseError, ProductNotFoundError
from parsers.utils import first_str, parse_decimal, parse_int, parse_rating
from schemas.product import Marketplace, ProductInfo

_ARTICLE_RE: Final = re.compile(r"(?:/catalog/|[?&](?:nm|card|nmId)=)(\d{5,12})", re.IGNORECASE)
_CARD_API: Final = "https://card.wb.ru/cards/v2/detail"
_CARD_PARAMS: Final[dict[str, str]] = {
    "appType": "1",
    "curr": "rub",
    "dest": "-1257786",  # Moscow; determines which warehouse prices are returned
    "spp": "30",
    "ab_testing": "false",
}
_API_HEADERS: Final[dict[str, str]] = {
    "accept": "*/*",
    "origin": "https://www.wildberries.ru",
    "referer": "https://www.wildberries.ru/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
}
# Upper bound of `vol` for each basket host, in order. Index + 1 == basket number.
_BASKET_BOUNDS: Final[tuple[int, ...]] = (
    143,
    287,
    431,
    719,
    1007,
    1061,
    1115,
    1169,
    1313,
    1601,
    1655,
    1919,
    2045,
    2189,
    2405,
    2621,
    2837,
    3053,
    3269,
    3485,
    3701,
    3917,
    4133,
    4349,
    4565,
)
_IMAGE_VARIANTS: Final[tuple[str, ...]] = ("big/1.webp", "c516x688/1.jpg", "big/1.jpg")


class WildberriesParser(BaseParser):
    """Parses ``wildberries.ru`` product links through the public card API."""

    marketplace: ClassVar[Marketplace] = Marketplace.WILDBERRIES
    domains: ClassVar[tuple[str, ...]] = (
        "wildberries.ru",
        "wildberries.by",
        "wildberries.kz",
        "wb.ru",
    )

    async def parse(self, url: str) -> ProductInfo:
        self.ensure_supported(url)
        article = self._extract_article(url)
        product = await self._fetch_card(article)
        image_url = await self._resolve_image(article)

        price, original_price = self._extract_prices(product)
        title = first_str(product.get("name"), product.get("imt_name"))
        if title is None:
            raise ParserResponseError(f"card {article} has no name field")

        brand = first_str(product.get("brand"))
        seller = first_str(product.get("supplier"), brand)

        return ProductInfo(
            title=title if brand is None or brand in title else f"{brand} / {title}",
            price=price,
            original_price=original_price,
            currency="₽",
            rating=parse_rating(product.get("reviewRating") or product.get("rating")),
            reviews_count=parse_int(product.get("feedbacks") or product.get("nmFeedbacks")),
            image_url=image_url,
            seller_name=seller,
            marketplace_name=self.marketplace,
            url=self.canonical_url(article),
            product_id=article,
            in_stock=self._in_stock(product),
        )

    # ------------------------------------------------------------------ internals

    @staticmethod
    def canonical_url(article: str) -> str:
        return f"https://www.wildberries.ru/catalog/{article}/detail.aspx"

    @staticmethod
    def _extract_article(url: str) -> str:
        match = _ARTICLE_RE.search(url)
        if match is None:
            raise ProductNotFoundError(
                f"no article id in {url}",
                user_message="В ссылке Wildberries не нашёлся артикул товара.",
            )
        return match.group(1)

    async def _fetch_card(self, article: str) -> dict[str, Any]:
        """Fetch the card through the impersonating client.

        ``card.wb.ru`` sits behind a WAF that scores the TLS fingerprint: a plain HTTP client
        is answered with ``403`` regardless of headers, so this goes over curl_cffi like the
        other marketplaces.
        """
        query = urlencode({**_CARD_PARAMS, "nm": article})
        status, final_url, body = await self.browser.get(
            f"{_CARD_API}?{query}", headers=_API_HEADERS
        )
        if status == 404:
            raise ProductNotFoundError(f"card API answered 404 for article {article}")
        if status >= 400:
            raise ParserResponseError(f"card API answered HTTP {status} for {final_url}")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ParserResponseError("card API returned a non-JSON body") from exc
        if not isinstance(payload, dict):
            raise ParserResponseError("card API returned a non-object payload")

        container = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        products = container.get("products") if isinstance(container, dict) else None
        if not isinstance(products, list) or not products:
            raise ProductNotFoundError(f"card API knows nothing about article {article}")

        product = products[0]
        if not isinstance(product, dict):
            raise ParserResponseError("card API returned a malformed product entry")
        return product

    @staticmethod
    def _extract_prices(product: dict[str, Any]) -> tuple[Decimal, Decimal | None]:
        """Return ``(current, original)``. WB reports money in kopecks."""
        sizes = product.get("sizes")
        if isinstance(sizes, list):
            for size in sizes:
                price = size.get("price") if isinstance(size, dict) else None
                if not isinstance(price, dict):
                    continue
                current = parse_decimal(price.get("product") or price.get("total"))
                original = parse_decimal(price.get("basic"))
                if current is not None:
                    return current / 100, (original / 100 if original is not None else None)

        current = parse_decimal(product.get("salePriceU"))
        original = parse_decimal(product.get("priceU"))
        if current is None:
            raise ProductNotFoundError(
                "card has no price — the item is most likely out of stock",
                user_message="Товара нет в наличии: Wildberries не отдаёт цену.",
            )
        return current / 100, (original / 100 if original is not None else None)

    @staticmethod
    def _in_stock(product: dict[str, Any]) -> bool:
        sizes = product.get("sizes")
        if not isinstance(sizes, list):
            return True
        for size in sizes:
            stocks = size.get("stocks") if isinstance(size, dict) else None
            if isinstance(stocks, list) and stocks:
                return True
        return False

    @staticmethod
    def _basket_host(article: int) -> str:
        vol = article // 100_000
        for index, bound in enumerate(_BASKET_BOUNDS, start=1):
            if vol <= bound:
                return f"basket-{index:02d}"
        return f"basket-{len(_BASKET_BOUNDS) + 1:02d}"

    def _image_candidates(self, article: str) -> list[str]:
        number = int(article)
        vol, part = number // 100_000, number // 1_000
        host = self._basket_host(number)
        base = f"https://{host}.wbbasket.ru/vol{vol}/part{part}/{number}/images"
        return [f"{base}/{variant}" for variant in _IMAGE_VARIANTS]

    async def _resolve_image(self, article: str) -> str | None:
        """Probe CDN variants and return the first one that actually exists."""
        candidates = self._image_candidates(article)
        for candidate in candidates:
            try:
                response = await self.http.head(candidate, timeout=5.0)
            except (httpx.HTTPError, asyncio.TimeoutError):  # noqa: UP041
                continue
            if response.status_code == 200:
                return candidate
        return candidates[0] if candidates else None
