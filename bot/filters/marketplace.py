"""Message filter that pulls a supported marketplace link out of an update."""

from __future__ import annotations

import re
from typing import Any, Final

from aiogram.filters import BaseFilter
from aiogram.types import Message, MessageEntity

from services.product_service import ProductService

_URL_RE: Final = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_BARE_DOMAIN_RE: Final = re.compile(
    r"\b(?:www\.)?(?:wildberries\.(?:ru|by|kz)|ozon\.(?:ru|by|kz)|market\.yandex\.(?:ru|by))"
    r"/[^\s<>\"']+",
    re.IGNORECASE,
)


class MarketplaceLinkFilter(BaseFilter):
    """Passes when the message contains a link one of the parsers can handle.

    On success the handler receives the extracted URL as ``product_url``.
    ``product_service`` is injected by aiogram from the dispatcher workflow data.
    """

    async def __call__(
        self, message: Message, product_service: ProductService
    ) -> bool | dict[str, Any]:
        for candidate in iter_urls(message):
            if product_service.supports(candidate):
                return {"product_url": candidate}
        return False


class ContainsURLFilter(BaseFilter):
    """Passes when the message contains any URL at all (supported or not)."""

    async def __call__(self, message: Message) -> bool:
        return any(True for _ in iter_urls(message))


def iter_urls(message: Message) -> list[str]:
    """Collect every URL in *message*, preferring Telegram entities over plain regex."""
    text = message.text or message.caption or ""
    entities: list[MessageEntity] = list(message.entities or message.caption_entities or [])

    found: list[str] = []
    for entity in entities:
        if entity.type == "text_link" and entity.url:
            found.append(entity.url)
        elif entity.type == "url":
            found.append(entity.extract_from(text))

    found.extend(_URL_RE.findall(text))
    found.extend(f"https://{match}" for match in _BARE_DOMAIN_RE.findall(text))

    unique: list[str] = []
    seen: set[str] = set()
    for url in found:
        normalised = url.strip().rstrip(".,;)]»")
        if normalised and normalised not in seen:
            seen.add(normalised)
            unique.append(normalised)
    return unique
