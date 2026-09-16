"""Parser error hierarchy.

Every error carries a ``user_message`` that is safe to show in Telegram, so the bot layer
never has to inspect exception types to build a reply.
"""

from __future__ import annotations


class ParserError(Exception):
    """Base class for every recoverable parsing failure."""

    user_message: str = "Не удалось получить данные о товаре. Попробуйте ещё раз позже."

    def __init__(self, message: str = "", *, user_message: str | None = None) -> None:
        super().__init__(message or self.user_message)
        if user_message is not None:
            self.user_message = user_message


class UnsupportedURLError(ParserError):
    """URL does not belong to any registered marketplace."""

    user_message = (
        "Я не умею разбирать такие ссылки. Поддерживаются Wildberries, Ozon и Яндекс Маркет."
    )


class ProductNotFoundError(ParserError):
    """Marketplace answered 404 / empty payload — the card no longer exists."""

    user_message = "Товар не найден — возможно, он снят с продажи или ссылка устарела."


class MarketplaceBlockedError(ParserError):
    """Anti-bot protection kicked in (captcha, 403, challenge page)."""

    user_message = "Маркетплейс закрыл доступ к карточке (антибот). Попробуйте через пару минут."


class ParserTimeoutError(ParserError):
    """Marketplace did not answer in time."""

    user_message = "Маркетплейс не ответил вовремя. Попробуйте ещё раз."


class ParserResponseError(ParserError):
    """Response arrived but could not be interpreted (bad status, broken JSON, layout change)."""

    user_message = "Маркетплейс вернул неожиданный ответ. Мы уже чиним парсер."
