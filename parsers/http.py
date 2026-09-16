"""HTTP plumbing shared by parsers.

Two transports are used side by side:

* :class:`httpx.AsyncClient` — plain JSON APIs that do not fingerprint the client
  (Wildberries card API, image CDNs).
* :class:`curl_cffi.requests.AsyncSession` — pages behind Cloudflare/anti-bot, where the
  TLS/JA3 fingerprint of a real browser is required (Ozon, Yandex Market).
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any, Final, Self

import httpx
from curl_cffi.requests import AsyncSession, Response

from parsers.exceptions import MarketplaceBlockedError, ParserResponseError, ParserTimeoutError

try:  # curl_cffi >= 0.7 moved its exception module around a couple of times.
    from curl_cffi.requests.exceptions import RequestException as CurlError
except ImportError:  # pragma: no cover - depends on the installed curl_cffi build
    from curl_cffi.requests.errors import RequestsError as CurlError

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


class BrowserSession:
    """Thin async wrapper around ``curl_cffi`` with a single lazily created session."""

    def __init__(self, *, impersonate: str, timeout: float) -> None:
        self._impersonate = impersonate
        self._timeout = timeout
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
        if response.status_code in _BLOCKED_STATUSES or _looks_like_captcha(final_url, text):
            raise MarketplaceBlockedError(f"blocked at {final_url} (HTTP {response.status_code})")
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


def _looks_like_captcha(url: str, body: str) -> bool:
    haystack = f"{url}\n{body[:2048]}".lower()
    return any(marker in haystack for marker in _CAPTCHA_MARKERS)


def create_http_client(timeout: float) -> httpx.AsyncClient:
    """Build the shared plain-HTTP client (JSON APIs, image downloads)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        headers={
            "accept": "application/json, text/plain, */*",
            "accept-language": BROWSER_HEADERS["accept-language"],
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
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
        raise MarketplaceBlockedError(f"blocked at {url} (HTTP {response.status_code})")
    if response.status_code >= 500:
        raise ParserResponseError(f"{url} answered HTTP {response.status_code}")

    try:
        return response.json()
    except ValueError as exc:
        raise ParserResponseError(f"{url} returned non-JSON body") from exc
