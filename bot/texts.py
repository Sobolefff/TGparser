"""User-facing copy and HTML caption rendering."""

from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Final

from schemas.product import ProductInfo

#: Telegram limits photo captions to 1024 characters and text messages to 4096.
CAPTION_LIMIT: Final = 1024
_MIN_TITLE_LENGTH: Final = 24

START_TEXT: Final = (
    "👋 Привет! Пришли мне ссылку на товар — я соберу карточку с ценой, скидкой, "
    "рейтингом и продавцом.\n\n"
    "Поддерживаю:\n"
    "• 🟣 <b>Wildberries</b>\n"
    "• 🔵 <b>Ozon</b>\n"
    "• 🟡 <b>Яндекс Маркет</b>\n\n"
    "Просто отправь ссылку в чат."
)

HELP_TEXT: Final = (
    "<b>Как пользоваться</b>\n\n"
    "1. Скопируй ссылку на товар в приложении или на сайте маркетплейса.\n"
    "2. Отправь её сюда — можно вместе с текстом, я найду ссылку сам.\n"
    "3. Получишь карточку с фото, ценой, скидкой, рейтингом и кнопкой в магазин.\n\n"
    "Карточки кэшируются на час, поэтому повторный запрос приходит мгновенно.\n"
    "Команда /start — начать заново."
)

NO_LINK_TEXT: Final = (
    "Я не нашёл в сообщении ссылку на товар 🤔\n\n"
    "Пришли ссылку на Wildberries, Ozon или Яндекс Маркет — например, "
    "<code>https://www.wildberries.ru/catalog/12345678/detail.aspx</code>"
)

GENERIC_ERROR_TEXT: Final = "Что-то пошло не так. Попробуйте ещё раз через минуту."

THROTTLED_TEXT: Final = "Слишком часто 😅 Подожди пару секунд и пришли ссылку снова."


def render_caption(product: ProductInfo, *, limit: int = CAPTION_LIMIT) -> str:
    """Render the product card as Telegram-flavoured HTML, fitting it into *limit*.

    Only the title is shortened on overflow, so the markup always stays balanced.
    """
    caption = _build(product, product.title)
    if _tg_length(caption) <= limit:
        return caption

    overflow = _tg_length(caption) - limit + 1
    keep = max(_MIN_TITLE_LENGTH, len(product.title) - overflow)
    return _build(product, product.title[:keep].rstrip() + "…")


def _tg_length(text: str) -> int:
    """Length in UTF-16 code units — the unit Telegram counts its limits in.

    Emoji live outside the BMP and cost two units each, so plain ``len()`` underestimates.
    """
    return len(text.encode("utf-16-le")) // 2


def _build(product: ProductInfo, title: str) -> str:
    lines: list[str] = [f"🛍 <b>{escape(title)}</b>", "", _price_line(product)]

    rating_line = _rating_line(product)
    if rating_line:
        lines.append(rating_line)
    if product.seller_name:
        lines.append(f"🏪 Продавец: <b>{escape(product.seller_name)}</b>")
    if not product.in_stock:
        lines.append("🚫 <b>Нет в наличии</b>")

    lines += ["", f"📦 {escape(product.marketplace_name.value)}"]
    return "\n".join(lines)


def _price_line(product: ProductInfo) -> str:
    currency = escape(product.currency)
    parts = [f"💰 <b>{_money(product.price)} {currency}</b>"]
    if product.original_price is not None:
        parts.append(f"<s>{_money(product.original_price)} {currency}</s>")
    discount = product.discount_percent
    if discount is not None:
        parts.append(f"🔥 <b>−{discount}%</b>")
    return "  ".join(parts)


def _rating_line(product: ProductInfo) -> str:
    chunks: list[str] = []
    if product.rating is not None:
        chunks.append(f"⭐ <b>{product.rating:.1f}</b>")
    if product.reviews_count is not None:
        count = product.reviews_count
        chunks.append(f"💬 {_group(count)} {_plural_reviews(count)}")
    return "  ·  ".join(chunks)


def _group(value: int) -> str:
    """Group thousands with a thin space: ``1245`` -> ``1 245``."""
    return f"{value:,}".replace(",", " ")


def _money(value: Decimal) -> str:
    """Format money with grouped thousands, dropping a meaningless ``,00`` tail."""
    whole, _, fraction = f"{value.quantize(Decimal('0.01')):.2f}".partition(".")
    grouped = _group(int(whole))
    return grouped if fraction == "00" else f"{grouped},{fraction}"


def _plural_reviews(count: int) -> str:
    tail_two, tail_one = count % 100, count % 10
    if 11 <= tail_two <= 14:
        return "отзывов"
    if tail_one == 1:
        return "отзыв"
    if 2 <= tail_one <= 4:
        return "отзыва"
    return "отзывов"
