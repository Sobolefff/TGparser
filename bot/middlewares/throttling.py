"""Per-user rate limiting backed by Redis."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from redis.asyncio import Redis
from redis.exceptions import RedisError

from bot.texts import THROTTLED_TEXT

logger = logging.getLogger(__name__)


class ThrottlingMiddleware(BaseMiddleware):
    """Drops messages that arrive faster than ``rate`` seconds apart, per user.

    The user is warned once per throttling window; further messages are dropped silently.
    If Redis is unavailable the middleware fails open and lets the update through.
    """

    def __init__(self, redis: Redis, *, rate: float, prefix: str = "throttle") -> None:
        self._redis = redis
        self._rate_ms = max(int(rate * 1000), 1)
        self._prefix = prefix

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or self._rate_ms <= 1:
            return await handler(event, data)

        if await self._acquire(f"{self._prefix}:{user.id}", self._rate_ms):
            return await handler(event, data)

        logger.debug("throttled user %s", user.id)
        warn_key = f"{self._prefix}:warn:{user.id}"
        if isinstance(event, Message) and await self._acquire(warn_key, self._rate_ms * 5):
            await event.answer(THROTTLED_TEXT)
        return None

    async def _acquire(self, key: str, ttl_ms: int) -> bool:
        """Return ``True`` when the caller is allowed to proceed (token acquired)."""
        try:
            return bool(await self._redis.set(key, "1", px=ttl_ms, nx=True))
        except RedisError:
            logger.warning("redis unavailable, throttling disabled for this update")
            return True
