"""HTTP plumbing shared by parsers.

Every marketplace request goes through :class:`curl_cffi.requests.AsyncSession` with a real
Chrome impersonation profile: Wildberries, Ozon and Yandex Market all run WAFs that score
the TLS/JA3 fingerprint, and a plain client is rejected with ``403`` before the headers are
even read. :class:`httpx.AsyncClient` is kept for traffic that is not fingerprinted — image
CDNs and HEAD probes.

Both transports honour ``PARSER_PROXY``, which is separate from ``TELEGRAM_PROXY``: the bot
may need one exit point for Telegram and a different one for the marketplaces.
"""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType
from typing import Any, Final, Self

import httpx
from curl_cffi.requests import AsyncSession, Response

from parsers.exceptions import MarketplaceBlockedError, ParserResponseError, ParserTimeoutError

try:  # curl_cffi >= 0.7 moved its exception module around a couple of times.
    from curl_cffi.requests.exceptions import RequestException as CurlError
except ImportError:  # pragma: no cover - depends on the installed curl_cffi build
    from curl_cffi.requests.errors import RequestsError as CurlError

logger = logging.getLogger(__name__)

USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BROWSER_HEADERS: Final[dict[str, str]] = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
    "image/webp,image/apng,*/*;q=0.8",
    "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}

_BLOCKED_STATUSES: Final[frozenset[int]] = frozenset({403, 429, 451})
_CAPTCHA_MARKERS: Final[tuple[str, ...]] = (
    "showcaptcha",
    "smartcaptcha",
    "/challenge",
    "checking your browser",
    "доступ ограничен",
)


def _blocked(url: str, status: int, *, hint: str = "") -> MarketplaceBlockedError:
    """Build a blocked-access error and log enough context to act on it."""
    logger.warning("blocked by marketplace: HTTP %s at %s %s", status, url, hint)
    return MarketplaceBlockedError(f"HTTP {status} at {url} {hint}".strip())


class BrowserSession:
    """Thin async wrapper around ``curl_cffi`` with a single lazily created session."""

    def __init__(self, *, impersonate: str, timeout: float, proxy: str | None = None) -> None:
        self._impersonate = impersonate
        self._timeout = timeout
        self._proxy = proxy
        self._session: AsyncSession[Response] | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> AsyncSession[Response]:
        if self._session is None:
            async with self._lock:
                if self._session is None:
                    self._session = AsyncSession(
                        impersonate=self._impersonate,
                        timeout=self._timeout,
                        headers=BROWSER_HEADERS,
                        proxy=self._proxy,
                        verify=True,
                    )
        return self._session

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allow_redirects: bool = True,
    ) -> tuple[int, str, str]:
        """GET *url* and return ``(status_code, final_url, text)``."""
        session = await self._ensure()
        try:
            response = await session.get(
                url,
                headers=headers,
                allow_redirects=allow_redirects,
                timeout=self._timeout,
            )
        except asyncio.TimeoutError as exc:  # noqa: UP041 - curl_cffi raises the builtin alias
            raise ParserTimeoutError(f"timeout while fetching {url}") from exc
        except CurlError as exc:
            message = str(exc).lower()
            if "timed out" in message or "timeout" in message:
                raise ParserTimeoutError(f"timeout while fetching {url}") from exc
            raise ParserResponseError(f"transport error for {url}: {exc}") from exc

        text = response.text or ""
        final_url = str(response.url)
        if response.status_code in _BLOCKED_STATUSES:
            raise _blocked(final_url, response.status_code, hint=f"({_server_of(response)})")
        if _looks_like_captcha(final_url, text):
            raise _blocked(final_url, response.status_code, hint="(captcha challenge)")
        return response.status_code, final_url, text

    async def resolve(self, url: str) -> str:
        """Follow redirects of a short link and return the final URL."""
        _, final_url, _ = await self.get(url)
        return final_url

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


def _server_of(response: Response) -> str:
    server = response.headers.get("server") or "unknown server"
    return f"server={server}"


def _looks_like_captcha(url: str, body: str) -> bool:
    haystack = f"{url}\n{body[:2048]}".lower()
    return any(marker in haystack for marker in _CAPTCHA_MARKERS)


def create_http_client(timeout: float, proxy: str | None = None) -> httpx.AsyncClient:
    """Build the plain-HTTP client used for image downloads and HEAD probes."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        proxy=proxy,
        headers={
            "accept": "application/json, text/plain, */*",
            "accept-language": BROWSER_HEADERS["accept-language"],
            "user-agent": USER_AGENT,
        },
        limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
    )


async def get_json(client: httpx.AsyncClient, url: str, **kwargs: Any) -> Any:
    """GET *url* and decode JSON, translating transport failures into parser errors."""
    try:
        response = await client.get(url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ParserTimeoutError(f"timeout while fetching {url}") from exc
    except httpx.HTTPError as exc:
        raise ParserResponseError(f"transport error for {url}: {exc}") from exc

    if response.status_code in _BLOCKED_STATUSES:
        raise _blocked(url, response.status_code)
    if response.status_code >= 500:
        raise ParserResponseError(f"{url} answered HTTP {response.status_code}")

    try:
        return response.json()
    except ValueError as exc:
        raise ParserResponseError(f"{url} returned non-JSON body") from exc
