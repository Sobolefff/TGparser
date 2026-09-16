"""aiogram routers, assembled in priority order."""

from aiogram import Router

from bot.handlers import common, product
from bot.handlers.errors import on_unhandled_error


def build_router() -> Router:
    """Combine every feature router into the single root router.

    Order matters: commands first, then link handling, then the generic fallbacks that
    live inside :mod:`bot.handlers.product`. The error observer is registered on the root
    itself so that it catches failures bubbling up from every nested router.
    """
    root = Router(name="root")
    root.include_router(common.router)
    root.include_router(product.router)
    root.errors.register(on_unhandled_error)
    return root


__all__ = ["build_router"]
