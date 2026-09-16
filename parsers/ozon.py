"""Ozon strategy.

Ozon fingerprints TLS and rejects plain HTTP clients, so every request goes through
``curl_cffi`` with a real Chrome impersonation profile. Instead of scraping the rendered
page we call the composer endpoint used by the site's own SPA
(``/api/entrypoint-api.bx/page/json/v2``) and read the widget states it returns.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, ClassVar, Final
from urllib.parse import quote, urlsplit

from parsers.base import BaseParser
from parsers.exceptions import ParserResponseError, ProductNotFoundError
from parsers.utils import (
    extract_json_ld,
    find_product_entity,
    first_str,
    parse_decimal,
    parse_int,
    parse_rating,
)
from schemas.product import Marketplace, ProductInfo

_COMPOSER: Final = "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2?url="
_PRODUCT_PATH_RE: Final = re.compile(r"/(product|t|context)/", re.IGNORECASE)
_PRODUCT_ID_RE: Final = re.compile(r"/product/(?:[^/?#]*-)?(\d{5,12})")
_TITLE_SUFFIX: Final = " - купить по выгодной цене в интернет-магазине OZON"

_API_HEADERS: Final[dict[str, str]] = {
    "accept": "application/json",
    "x-o3-app-name": "dweb_bx",
    "x-o3-app-version": "release_10-9-2024",
    "x-requested-with": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "referer": "https://www.ozon.ru/",
}

_TITLE_WIDGETS: Final = ("webProductHeading",)
_PRICE_WIDGETS: Final = ("webPrice", "webSale", "webOutOfStock")
_GALLERY_WIDGETS: Final = ("webGallery", "webAspects")
_SCORE_WIDGETS: Final = ("webSingleProductScore", "webReviewProductScore", "webProductScore")
_SELLER_WIDGETS: Final = ("webCurrentSeller", "webStickyProducts", "webSellerInfo")


class OzonParser(BaseParser):
    """Parses ``ozon.ru`` product links via the composer API behind TLS impersonation."""

    marketplace: ClassVar[Marketplace] = Marketplace.OZON
    domains: ClassVar[tuple[str, ...]] = ("ozon.ru", "ozon.by", "ozon.kz", "ozon.com")
    path_pattern: ClassVar[re.Pattern[str] | None] = _PRODUCT_PATH_RE

    async def parse(self, url: str) -> ProductInfo:
        self.ensure_supported(url)
        page_url = await self._resolve_short_link(url)
        payload = await self._fetch_composer(page_url)
        states = self._widget_states(payload)

        price, original_price = self._extract_prices(states, payload)
        rating, reviews = self._extract_score(states, payload)

        return ProductInfo(
            title=self._extract_title(states, payload),
            price=price,
            original_price=original_price,
            currency="₽",
            rating=rating,
            reviews_count=reviews,
            image_url=self._extract_image(states, payload),
            seller_name=self._extract_seller(states),
            marketplace_name=self.marketplace,
            url=page_url,
            product_id=self._extract_product_id(page_url),
            in_stock=self._find_widget(states, ("webOutOfStock",)) is None,
        )

    # ------------------------------------------------------------------ transport

    async def _resolve_short_link(self, url: str) -> str:
        """Expand ``ozon.ru/t/...`` share links into the canonical product URL."""
        if "/t/" not in url and "/context/" not in url:
            return url
        return await self.browser.resolve(url)

    async def _fetch_composer(self, url: str) -> dict[str, Any]:
        parts = urlsplit(url)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        api_url = _COMPOSER + quote(path, safe="")

        status, _, body = await self.browser.get(api_url, headers={**_API_HEADERS, "referer": url})
        if status == 404:
            raise ProductNotFoundError(f"ozon answered 404 for {url}")
        if status >= 400:
            raise ParserResponseError(f"ozon composer answered HTTP {status} for {url}")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ParserResponseError("ozon composer returned a non-JSON body") from exc
        if not isinstance(payload, dict):
            raise ParserResponseError("ozon composer returned a non-object payload")

        page_info = payload.get("pageInfo")
        if isinstance(page_info, dict) and int(page_info.get("statusCode") or 200) == 404:
            raise ProductNotFoundError(f"ozon reports 404 for {url}")
        return payload

    @staticmethod
    def _widget_states(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Decode ``widgetStates``: ``{"webPrice-123-default-1": "<json as string>"}``."""
        raw = payload.get("widgetStates")
        if not isinstance(raw, dict):
            return {}
        states: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                states[key] = decoded
        return states

    @staticmethod
    def _find_widget(
        states: dict[str, dict[str, Any]], prefixes: tuple[str, ...]
    ) -> dict[str, Any] | None:
        for prefix in prefixes:
            for key, state in states.items():
                if key.startswith(prefix):
                    return state
        return None

    # ------------------------------------------------------------------ extraction

    @staticmethod
    def _json_ld(payload: dict[str, Any]) -> dict[str, Any] | None:
        """Ozon duplicates the card as JSON-LD inside ``seo.script`` — a solid fallback."""
        seo = payload.get("seo")
        if not isinstance(seo, dict):
            return None
        scripts = seo.get("script")
        if not isinstance(scripts, list):
            return None
        for script in scripts:
            inner = script.get("innerHTML") if isinstance(script, dict) else None
            if not isinstance(inner, str):
                continue
            wrapped = '<script type="application/ld+json">' + inner + "</script>"
            entity = find_product_entity(extract_json_ld(wrapped))
            if entity is not None:
                return entity
        return None

    def _extract_title(self, states: dict[str, dict[str, Any]], payload: dict[str, Any]) -> str:
        heading = self._find_widget(states, _TITLE_WIDGETS) or {}
        ld = self._json_ld(payload) or {}
        seo = payload.get("seo")
        title = first_str(
            heading.get("title"),
            ld.get("name"),
            seo.get("title") if isinstance(seo, dict) else None,
        )
        if title is None:
            raise ParserResponseError("ozon payload contains no product title")
        return title.removesuffix(_TITLE_SUFFIX)

    def _extract_prices(
        self, states: dict[str, dict[str, Any]], payload: dict[str, Any]
    ) -> tuple[Decimal, Decimal | None]:
        widget = self._find_widget(states, _PRICE_WIDGETS) or {}
        current = parse_decimal(widget.get("price"))
        original = parse_decimal(widget.get("originalPrice"))

        if current is None:
            offers = (self._json_ld(payload) or {}).get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                current = parse_decimal(offers.get("price") or offers.get("lowPrice"))

        if current is None:
            raise ProductNotFoundError(
                "ozon returned no price for the card",
                user_message="Ozon не отдал цену — скорее всего, товара нет в наличии.",
            )
        return current, original

    def _extract_image(
        self, states: dict[str, dict[str, Any]], payload: dict[str, Any]
    ) -> str | None:
        gallery = self._find_widget(states, _GALLERY_WIDGETS) or {}
        cover = first_str(gallery.get("coverImage"))
        if cover is not None:
            return cover

        images = gallery.get("images")
        if isinstance(images, list):
            for image in images:
                src = image.get("src") if isinstance(image, dict) else image
                found = first_str(src)
                if found is not None:
                    return found

        image = (self._json_ld(payload) or {}).get("image")
        if isinstance(image, list):
            return first_str(*image)
        return first_str(image)

    def _extract_score(
        self, states: dict[str, dict[str, Any]], payload: dict[str, Any]
    ) -> tuple[float | None, int | None]:
        widget = self._find_widget(states, _SCORE_WIDGETS) or {}
        rating = parse_rating(widget.get("totalScore") or widget.get("score"))
        reviews = parse_int(widget.get("reviewsCount") or widget.get("commentsCount"))

        if rating is None or reviews is None:
            aggregate = (self._json_ld(payload) or {}).get("aggregateRating")
            if isinstance(aggregate, dict):
                if rating is None:
                    rating = parse_rating(aggregate.get("ratingValue"))
                if reviews is None:
                    reviews = parse_int(
                        aggregate.get("reviewCount") or aggregate.get("ratingCount")
                    )
        return rating, reviews

    def _extract_seller(self, states: dict[str, dict[str, Any]]) -> str | None:
        widget = self._find_widget(states, _SELLER_WIDGETS) or {}
        seller = widget.get("seller")
        if isinstance(seller, dict):
            found = first_str(seller.get("name"), seller.get("title"))
            if found is not None:
                return found
        return first_str(widget.get("name"), widget.get("title"))

    @staticmethod
    def _extract_product_id(url: str) -> str | None:
        match = _PRODUCT_ID_RE.search(url)
        return match.group(1) if match is not None else None
