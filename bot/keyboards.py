"""Inline keyboards."""

from __future__ import annotations

from typing import Final

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from schemas.product import Marketplace, ProductInfo

_BUTTON_LABELS: Final[dict[Marketplace, str]] = {
    Marketplace.WILDBERRIES: "🟣 Открыть на Wildberries",
    Marketplace.OZON: "🔵 Открыть на Ozon",
    Marketplace.YANDEX_MARKET: "🟡 Открыть на Яндекс Маркете",
}


def product_keyboard(product: ProductInfo) -> InlineKeyboardMarkup:
    """A single deep link back to the product page on the marketplace."""
    builder = InlineKeyboardBuilder()
    label = _BUTTON_LABELS.get(product.marketplace_name, "Открыть в магазине")
    builder.button(text=label, url=str(product.url))
    builder.adjust(1)
    return builder.as_markup()
