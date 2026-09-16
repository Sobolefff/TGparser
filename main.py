"""Entry point: wire the dependencies together and start long polling."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher

from bot import BOT_COMMANDS, create_bot, create_dispatcher
from config import Settings, get_settings
from parsers.dispatcher import ParserDispatcher
from services.cache import ProductCache, create_redis
from services.product_service import ProductService

logger = logging.getLogger("tgparser")


def setup_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def on_startup(bot: Bot, cache: ProductCache) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
    me = await bot.get_me()
    if await cache.ping():
        logger.info("redis is reachable, cards are cached for %s seconds", cache.ttl)
    else:
        logger.warning("redis is unreachable — running without cache")
    logger.info("bot @%s is up and polling", me.username)


async def run() -> None:
    settings = get_settings()
    setup_logging(settings)

    redis = create_redis(settings)
    cache = ProductCache(redis, ttl=settings.cache_ttl, prefix=settings.cache_prefix)
    parser_dispatcher = ParserDispatcher.from_settings(settings)
    product_service = ProductService(dispatcher=parser_dispatcher, cache=cache, settings=settings)

    bot: Bot = create_bot(settings)
    dispatcher: Dispatcher = create_dispatcher(
        settings=settings, redis=redis, product_service=product_service
    )

    try:
        await on_startup(bot, cache)
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        logger.info("shutting down")
        await product_service.aclose()
        await redis.aclose()
        await bot.session.close()


def main() -> None:
    with contextlib.suppress(ImportError):
        import uvloop  # available on Linux/macOS only

        uvloop.install()

    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(run())


if __name__ == "__main__":
    main()
