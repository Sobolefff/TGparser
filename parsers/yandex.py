"""Yandex Market strategy.

Yandex Market has no open product API and guards the SPA with SmartCaptcha, so the page is
fetched with a TLS-impersonating client and read through the JSON-LD block that Market
renders server-side for search engines. Open Graph meta tags act as a second source.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, ClassVar, Final

from selectolax.lexbor import LexborHTMLParser

from parsers.base import BaseParser
from parsers.exceptions import ParserResponseError, ProductNotFoundError
from parsers.utils import (
    clean_url,
    extract_json_ld,
    find_product_entity,
    first_str,
    parse_decimal,
    parse_int,
    parse_rating,
)
from schemas.product import Marketplace, ProductInfo

_PRODUCT_PATH_RE: Final = re.compile(r"/(product|cc|offer|card)", re.IGNORECASE)
_PRODUCT_ID_RE: Final = re.compile(r"/product(?:--[^/?#]*)?/(\d{5,14})")
_TITLE_SUFFIX_RE: Final = re.compile(
    r"\s*[-—]\s*(купить|цены|отзывы).*$", re.IGNORECASE | re.DOTALL
)
_PAGE_HEADERS: Final[dict[str, str]] = {
    "referer": "https://market.yandex.ru/",
    "sec-fetch-site": "same-origin",
}


class YandexMarketParser(BaseParser):
    """Parses ``market.yandex.ru`` product links from server-rendered structured data."""

    marketplace: ClassVar[Marketplace] = Marketplace.YANDEX_MARKET
    domains: ClassVar[tuple[str, ...]] = ("market.yandex.ru", "market.yandex.by", "ya.cc")
    path_pattern: ClassVar[re.Pattern[str] | None] = _PRODUCT_PATH_RE

    async def parse(self, url: str) -> ProductInfo:
        self.ensure_supported(url)

        status, final_url, html = await self.browser.get(url, headers=_PAGE_HEADERS)
        if status == 404:
            raise ProductNotFoundError(f"yandex market answered 404 for {url}")
        if status >= 400:
            raise ParserResponseError(f"yandex market answered HTTP {status} for {url}")

        tree = LexborHTMLParser(html)
        entity = find_product_entity(extract_json_ld(html)) or {}
        offer = self._pick_offer(entity)
        meta = self._meta_tags(tree)

        title = first_str(entity.get("name"), meta.get("og:title"), self._document_title(tree))
        if title is None:
            raise ProductNotFoundError(
                f"no structured product data on {final_url}",
                user_message="Не удалось прочитать карточку Яндекс Маркета — попробуйте позже.",
            )

        price, original_price = self._extract_prices(offer)
        rating, reviews = self._extract_score(entity)

        return ProductInfo(
            title=_TITLE_SUFFIX_RE.sub("", title),
            price=price,
            original_price=original_price,
            currency=self._currency(offer),
            rating=rating,
            reviews_count=reviews,
            image_url=self._extract_image(entity, meta),
            seller_name=self._extract_seller(entity, offer),
            marketplace_name=self.marketplace,
            url=clean_url(final_url, drop_query=True),
            product_id=self._extract_product_id(final_url),
            in_stock=self._in_stock(offer),
        )

    # ------------------------------------------------------------------ extraction

    @staticmethod
    def _meta_tags(tree: LexborHTMLParser) -> dict[str, str]:
        tags: dict[str, str] = {}
        for node in tree.css("meta[property], meta[name]"):
            key = node.attributes.get("property") or node.attributes.get("name")
            content = node.attributes.get("content")
            if key and content:
                tags.setdefault(key, content)
        return tags

    @staticmethod
    def _document_title(tree: LexborHTMLParser) -> str | None:
        node = tree.css_first("title")
        return node.text(strip=True) if node is not None else None

    @staticmethod
    def _pick_offer(entity: dict[str, Any]) -> dict[str, Any]:
        """Return the (possibly aggregate) offer attached to the product entity."""
        offers = entity.get("offers")
        if isinstance(offers, list):
            offers = next((item for item in offers if isinstance(item, dict)), None)
        return offers if isinstance(offers, dict) else {}

    def _extract_prices(self, offer: dict[str, Any]) -> tuple[Decimal, Decimal | None]:
        current = parse_decimal(
            offer.get("price") or offer.get("lowPrice") or offer.get("lowprice")
        )
        if current is None:
            raise ProductNotFoundError(
                "yandex market offer carries no price",
                user_message="Яндекс Маркет не показывает цену — возможно, товара нет в продаже.",
            )
        original = parse_decimal(offer.get("highPrice") or offer.get("originalPrice"))
        return current, original

    @staticmethod
    def _currency(offer: dict[str, Any]) -> str:
        code = first_str(offer.get("priceCurrency"))
        return {"RUB": "₽", "BYN": "Br", "KZT": "₸", "USD": "$", "EUR": "€"}.get(
            (code or "RUB").upper(), code or "₽"
        )

    @staticmethod
    def _extract_score(entity: dict[str, Any]) -> tuple[float | None, int | None]:
        aggregate = entity.get("aggregateRating")
        if not isinstance(aggregate, dict):
            return None, None
        rating = parse_rating(aggregate.get("ratingValue"))
        reviews = parse_int(aggregate.get("reviewCount") or aggregate.get("ratingCount"))
        return rating, reviews

    @staticmethod
    def _extract_image(entity: dict[str, Any], meta: dict[str, str]) -> str | None:
        image = entity.get("image")
        if isinstance(image, list):
            image = first_str(*image)
        elif isinstance(image, dict):
            image = first_str(image.get("url"), image.get("contentUrl"))
        found = first_str(image, meta.get("og:image"))
        if found is None:
            return None
        return f"https:{found}" if found.startswith("//") else found

    @staticmethod
    def _extract_seller(entity: dict[str, Any], offer: dict[str, Any]) -> str | None:
        for source in (offer.get("seller"), offer.get("offeredBy"), entity.get("brand")):
            if isinstance(source, dict):
                found = first_str(source.get("name"))
                if found is not None:
                    return found
            found = first_str(source)
            if found is not None:
                return found
        return None

    @staticmethod
    def _in_stock(offer: dict[str, Any]) -> bool:
        availability = first_str(offer.get("availability")) or ""
        return "outofstock" not in availability.replace(" ", "").lower()

    @staticmethod
    def _extract_product_id(url: str) -> str | None:
        match = _PRODUCT_ID_RE.search(url)
        return match.group(1) if match is not None else None
