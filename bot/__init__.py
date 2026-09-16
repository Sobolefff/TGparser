"""Telegram layer: bot instance, dispatcher wiring, routers and middlewares."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from redis.asyncio import Redis

from bot.handlers import build_router
from bot.handlers.common import BOT_COMMANDS
from bot.middlewares.throttling import ThrottlingMiddleware
from config import Settings
from services.product_service import ProductService


def create_bot(settings: Settings) -> Bot:
    """Build the Bot with HTML parse mode and link previews disabled by default."""
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


def create_dispatcher(
    *,
    settings: Settings,
    redis: Redis,
    product_service: ProductService,
) -> Dispatcher:
    """Assemble the dispatcher: FSM storage, workflow data, middlewares and routers.

    The flow is stateless (one link in, one card out), so in-memory FSM storage is enough;
    Redis is reserved for the product cache and the throttler.
    """
    dispatcher = Dispatcher(storage=MemoryStorage())

    # Workflow data — injected into handlers and filters by parameter name.
    dispatcher["settings"] = settings
    dispatcher["product_service"] = product_service

    dispatcher.message.middleware(ThrottlingMiddleware(redis, rate=settings.throttle_rate))
    dispatcher.include_router(build_router())
    return dispatcher


__all__ = ["BOT_COMMANDS", "create_bot", "create_dispatcher"]
