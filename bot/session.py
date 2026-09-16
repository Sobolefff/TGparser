"""Telegram transport session.

Wraps :class:`~aiogram.client.session.aiohttp.AiohttpSession` so that proxy failures are
reported as ordinary network errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.methods import TelegramMethod
    from aiogram.methods.base import TelegramType

#: ``aiohttp_socks`` derives these straight from ``Exception`` — neither ``aiohttp.ClientError``
#: nor ``OSError`` — so aiogram does not recognise them as transport failures.
_PROXY_ERRORS = (ProxyConnectionError, ProxyTimeoutError, ProxyError)


class ResilientAiohttpSession(AiohttpSession):
    """Translates proxy transport errors into :class:`TelegramNetworkError`.

    Without this, a proxy that is down (or misconfigured) raises an exception aiogram does
    not catch: it escapes ``start_polling`` and kills the process, which under Docker turns
    into a restart loop. As a ``TelegramNetworkError`` it lands in aiogram's polling backoff
    instead, and the bot recovers by itself once the proxy is reachable again.
    """

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,  # noqa: ASYNC109 - signature fixed by the base class
    ) -> TelegramType:
        try:
            return await super().make_request(bot, method, timeout=timeout)
        except _PROXY_ERRORS as exc:
            raise TelegramNetworkError(
                method=method, message=f"proxy failure — {type(exc).__name__}: {exc}"
            ) from exc
