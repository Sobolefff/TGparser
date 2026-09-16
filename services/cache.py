"""Redis-backed cache for parsed product cards.

Keys are ``<prefix>:<md5(normalised url)>`` and hold the JSON dump of :class:`ProductInfo`
with a TTL of one hour by default. Redis is treated as a best-effort layer: if it is down,
the bot keeps working and simply parses every link from scratch.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Final

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from config import Settings
from parsers.utils import clean_url
from schemas.product import ProductInfo

logger = logging.getLogger(__name__)

_ENCODING: Final = "utf-8"


def create_redis(settings: Settings) -> Redis:
    """Build the async Redis client used by the whole application."""
    return Redis.from_url(
        settings.redis_url,
        encoding=_ENCODING,
        decode_responses=True,
        socket_timeout=3.0,
        socket_connect_timeout=3.0,
        health_check_interval=30,
    )


class ProductCache:
    """Stores product cards keyed by the MD5 hash of their URL."""

    def __init__(self, redis: Redis, *, ttl: int, prefix: str = "product") -> None:
        self._redis = redis
        self._ttl = ttl
        self._prefix = prefix

    @property
    def ttl(self) -> int:
        return self._ttl

    def key_for(self, url: str) -> str:
        """Return the Redis key for *url* (tracking params are stripped first)."""
        digest = hashlib.md5(clean_url(url).encode(_ENCODING)).hexdigest()  # noqa: S324
        return f"{self._prefix}:{digest}"

    async def get(self, url: str) -> ProductInfo | None:
        """Return a cached card, or ``None`` on a miss, invalid payload or Redis failure."""
        key = self.key_for(url)
        try:
            raw = await self._redis.get(key)
        except RedisError:
            logger.warning("redis GET failed for %s, falling back to live parsing", key)
            return None
        if raw is None:
            return None

        try:
            product = ProductInfo.model_validate_json(raw)
        except ValidationError:
            logger.warning("dropping cache entry %s: schema changed", key)
            await self.invalidate(url)
            return None

        logger.debug("cache hit for %s", key)
        return product

    async def set(self, product: ProductInfo) -> None:
        """Persist *product* under its own URL for :attr:`ttl` seconds."""
        key = self.key_for(str(product.url))
        try:
            await self._redis.set(key, product.model_dump_json(), ex=self._ttl)
        except RedisError:
            logger.warning("redis SET failed for %s, card will not be cached", key)

    async def set_alias(self, url: str, product: ProductInfo) -> None:
        """Cache *product* under the exact URL the user sent.

        Parsers canonicalise URLs (short links, ``?sku=`` variants), so the URL typed by the
        user and ``product.url`` often differ. Storing both keeps the next hit cheap.
        """
        await self.set(product)
        if self.key_for(url) != self.key_for(str(product.url)):
            try:
                await self._redis.set(self.key_for(url), product.model_dump_json(), ex=self._ttl)
            except RedisError:
                logger.warning("redis SET failed for alias of %s", url)

    async def invalidate(self, url: str) -> None:
        try:
            await self._redis.delete(self.key_for(url))
        except RedisError:
            logger.warning("redis DEL failed for %s", url)

    async def ping(self) -> bool:
        """Return ``True`` when Redis answers — used for the startup health check."""
        try:
            return bool(await self._redis.ping())
        except RedisError:
            return False
