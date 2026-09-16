"""Marketplace access diagnostics.

Runs the real parser strategies against known-good product links and reports what came
back, so a "Маркетплейс закрыл доступ" reply in the bot can be traced to a concrete cause:
a blocked server IP, a changed API schema, or a proxy that is not working.

Usage::

    docker compose exec bot python scripts/check_access.py     # inside the container
    uv run scripts/check_access.py                             # locally
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Settings, get_settings
from parsers.dispatcher import ParserDispatcher
from parsers.exceptions import (
    MarketplaceBlockedError,
    ParserError,
    ParserTimeoutError,
    ProductNotFoundError,
)
from parsers.http import BrowserSession

#: Long-lived popular listings; if one disappears, swap the URL — 404 is not a network issue.
SAMPLES: dict[str, str] = {
    "Wildberries": "https://www.wildberries.ru/catalog/171796130/detail.aspx",
    "Ozon": "https://www.ozon.ru/product/smartfon-apple-iphone-13-128gb-1409934826/",
    "Яндекс Маркет": "https://market.yandex.ru/product--apple-iphone-13-128gb/1757699150",
}

REACHABILITY: dict[str, str] = {
    "card.wb.ru": "https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm=171796130",
    "www.ozon.ru": "https://www.ozon.ru/",
    "market.yandex.ru": "https://market.yandex.ru/",
}


@dataclass(frozen=True, slots=True)
class Row:
    name: str
    verdict: str
    detail: str


async def probe_reachability(settings: Settings) -> list[Row]:
    """Raw GET per marketplace host through the same impersonating client the bot uses."""
    rows: list[Row] = []
    async with BrowserSession(
        impersonate=settings.impersonate,
        timeout=settings.request_timeout,
        proxy=settings.parser_proxy,
    ) as browser:
        for host, url in REACHABILITY.items():
            try:
                status, _, body = await browser.get(url)
            except MarketplaceBlockedError as exc:
                rows.append(Row(host, "BLOCKED", str(exc)))
            except ParserTimeoutError:
                rows.append(Row(host, "TIMEOUT", "no answer within the request timeout"))
            except ParserError as exc:
                rows.append(Row(host, "ERROR", f"{type(exc).__name__}: {exc}"))
            else:
                rows.append(Row(host, f"HTTP {status}", f"{len(body)} bytes"))
    return rows


async def probe_parsers(settings: Settings) -> list[Row]:
    """Full parse of a sample link per marketplace — exactly what the bot does."""
    rows: list[Row] = []
    async with ParserDispatcher.from_settings(settings) as dispatcher:
        for name, url in SAMPLES.items():
            try:
                product = await dispatcher.parse(url)
            except MarketplaceBlockedError as exc:
                rows.append(Row(name, "BLOCKED", str(exc)))
            except ProductNotFoundError as exc:
                rows.append(Row(name, "NOT FOUND", f"{exc} (try another sample URL)"))
            except ParserTimeoutError:
                rows.append(Row(name, "TIMEOUT", "no answer within the request timeout"))
            except ParserError as exc:
                rows.append(Row(name, "ERROR", f"{type(exc).__name__}: {exc}"))
            else:
                rows.append(
                    Row(name, "OK", f"{product.price} {product.currency} — {product.title[:48]}")
                )
    return rows


def render(title: str, rows: list[Row]) -> None:
    width = max((len(row.name) for row in rows), default=10)
    print(f"\n{title}")
    print("-" * (width + 62))
    for row in rows:
        print(f"{row.name:<{width}}  {row.verdict:<10}  {row.detail[:60]}")


def summarise(rows: list[Row]) -> None:
    blocked = [row.name for row in rows if row.verdict == "BLOCKED"]
    if blocked:
        print(
            "\nЗаблокирован доступ: "
            + ", ".join(blocked)
            + "\nIP сервера отклоняется WAF маркетплейса. Варианты: сменить локацию VPS "
            "или задать PARSER_PROXY в .env (см. DEPLOY.md)."
        )
    elif all(row.verdict == "OK" for row in rows):
        print("\nВсё доступно — парсинг должен работать.")


async def main() -> None:
    settings = get_settings()
    print(f"impersonate : {settings.impersonate}")
    print(f"parser proxy: {settings.parser_proxy or '— (прямое подключение)'}")

    render("Доступность хостов", await probe_reachability(settings))
    parser_rows = await probe_parsers(settings)
    render("Разбор карточек", parser_rows)
    summarise(parser_rows)


if __name__ == "__main__":
    asyncio.run(main())
