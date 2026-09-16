"""Catch-all error handler.

Any exception escaping a handler ends up here: it is logged with a traceback and the user
gets a plain apology instead of silence. The bot itself never stops polling.

The callback is registered on the root router by :func:`bot.handlers.build_router`, so it
sees failures from every nested router.
"""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent, Message

from bot.texts import GENERIC_ERROR_TEXT
from parsers.exceptions import ParserError

logger = logging.getLogger(__name__)


async def on_unhandled_error(event: ErrorEvent) -> bool:
    """Return ``True`` to mark the error as handled and keep polling."""
    exception = event.exception
    message = event.update.message

    if isinstance(exception, ParserError):
        logger.info("unhandled parser error: %s", exception)
        text = exception.user_message
    else:
        logger.exception("unhandled update error", exc_info=exception)
        text = GENERIC_ERROR_TEXT

    await _try_reply(message, text)
    return True


async def _try_reply(message: Message | None, text: str) -> None:
    if message is None:
        return
    try:
        await message.reply(text)
    except TelegramAPIError:
        logger.warning("could not deliver the error notice to chat %s", message.chat.id)
