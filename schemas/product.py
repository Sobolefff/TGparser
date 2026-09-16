"""Normalised product card produced by every parser strategy."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationInfo,
    field_validator,
    model_validator,
)


class Marketplace(StrEnum):
    """Supported marketplaces. Values are human-readable and go straight into the caption."""

    WILDBERRIES = "Wildberries"
    OZON = "Ozon"
    YANDEX_MARKET = "Яндекс Маркет"


Price = Annotated[Decimal, Field(ge=0, decimal_places=2)]

#: Hard limits mirrored by the ``max_length`` constraints of the corresponding fields.
_MAX_LENGTHS: Final[dict[str, int]] = {"title": 512, "seller_name": 256}


class ProductInfo(BaseModel):
    """Marketplace-agnostic product card.

    Everything except :attr:`title`, :attr:`price`, :attr:`marketplace_name` and :attr:`url`
    is optional: marketplaces regularly hide sellers, ratings or galleries, and a partially
    filled card is still worth showing to the user.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=512)
    price: Price
    original_price: Price | None = None
    currency: str = Field(default="₽", max_length=8)
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews_count: int | None = Field(default=None, ge=0)
    image_url: HttpUrl | None = None
    seller_name: str | None = Field(default=None, max_length=256)
    marketplace_name: Marketplace
    url: HttpUrl

    # Extras that are handy in the UI but not part of the minimal contract.
    product_id: str | None = Field(default=None, max_length=64)
    in_stock: bool = True
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("title", "seller_name", mode="before")
    @classmethod
    def _normalise_text(cls, value: object, info: ValidationInfo) -> object:
        """Collapse whitespace and clamp length.

        Marketplaces occasionally return SEO-bloated titles; a too-long title should shorten
        the card, not fail the whole parse.
        """
        if not isinstance(value, str):
            return value
        collapsed = " ".join(value.split())
        limit = _MAX_LENGTHS.get(info.field_name or "", 512)
        return collapsed if len(collapsed) <= limit else collapsed[: limit - 1].rstrip() + "…"

    @field_validator("price", "original_price", mode="before")
    @classmethod
    def _quantize(cls, value: object) -> object:
        if isinstance(value, int | float | str) and not isinstance(value, bool):
            return Decimal(str(value)).quantize(Decimal("0.01"))
        if isinstance(value, Decimal):
            return value.quantize(Decimal("0.01"))
        return value

    @model_validator(mode="after")
    def _drop_meaningless_original_price(self) -> Self:
        """``original_price`` only makes sense when it is strictly above the current one."""
        if self.original_price is not None and self.original_price <= self.price:
            object.__setattr__(self, "original_price", None)
        return self

    @property
    def discount_percent(self) -> int | None:
        """Discount in whole percent, or ``None`` when the item is sold at full price."""
        if self.original_price is None or self.original_price <= 0:
            return None
        discount = (self.original_price - self.price) / self.original_price * 100
        rounded = int(discount)
        return rounded or None

    @property
    def has_image(self) -> bool:
        return self.image_url is not None
