"""Product image download and normalisation.

Telegram refuses WebP in ``sendPhoto`` and both Wildberries and Ozon serve WebP by default,
so images are downloaded here and transcoded to JPEG before they are re-uploaded. Any
failure degrades gracefully to "no image" — the caller then sends a text-only card.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Final

import httpx

logger = logging.getLogger(__name__)

_PHOTO_TYPES: Final[frozenset[str]] = frozenset({"image/jpeg", "image/png"})
_MAX_SIDE: Final = 1600
_JPEG_QUALITY: Final = 88


@dataclass(frozen=True, slots=True)
class ImagePayload:
    """A downloaded image ready to be wrapped into an aiogram ``BufferedInputFile``."""

    content: bytes
    filename: str


class ImageFetcher:
    """Downloads product images and converts them into a Telegram-friendly format."""

    def __init__(self, client: httpx.AsyncClient, *, max_bytes: int) -> None:
        self._client = client
        self._max_bytes = max_bytes

    async def fetch(self, url: str, *, filename: str = "product") -> ImagePayload | None:
        """Return the image behind *url*, or ``None`` if it cannot be used as a photo."""
        raw = await self._download(url)
        if raw is None:
            return None

        content_type = raw[1]
        content = raw[0]
        if content_type in _PHOTO_TYPES:
            extension = "jpg" if content_type == "image/jpeg" else "png"
            return ImagePayload(content=content, filename=f"{filename}.{extension}")

        converted = await asyncio.to_thread(_to_jpeg, content)
        if converted is None:
            logger.info("cannot convert image %s (content-type=%s)", url, content_type)
            return None
        return ImagePayload(content=converted, filename=f"{filename}.jpg")

    async def _download(self, url: str) -> tuple[bytes, str] | None:
        try:
            response = await self._client.get(url, headers={"accept": "image/*,*/*"})
        except httpx.HTTPError as exc:
            logger.info("image download failed for %s: %s", url, exc)
            return None

        if response.status_code != 200:
            logger.info("image %s answered HTTP %s", url, response.status_code)
            return None
        if len(response.content) > self._max_bytes:
            logger.info("image %s is too large (%s bytes)", url, len(response.content))
            return None

        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        return response.content, content_type


def _to_jpeg(content: bytes) -> bytes | None:
    """Transcode any Pillow-readable image (WebP, AVIF via plugins, GIF...) to JPEG."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard dependency in practice
        logger.warning("Pillow is not installed, cannot transcode product images")
        return None

    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            rgb = image.convert("RGB")
            rgb.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
            return buffer.getvalue()
    except Exception:  # Pillow raises a wide zoo of errors on malformed input
        logger.info("Pillow could not decode the downloaded image", exc_info=True)
        return None
