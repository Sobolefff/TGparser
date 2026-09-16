"""Small pure helpers shared by the concrete parser strategies."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

__all__ = [
    "clean_url",
    "extract_json_ld",
    "find_product_entity",
    "first_str",
    "host_of",
    "parse_decimal",
    "parse_int",
    "parse_rating",
]

_TRACKING_PREFIXES = ("utm_", "_openstat")
_TRACKING_PARAMS = frozenset(
    {
        "from",
        "sh",
        "advert_id",
        "asb",
        "asb2",
        "cpm",
        "clid",
        "lr",
        "pp",
        "rs",
        "sid",
        "targetbid",
        "did",
        "fclid",
        "text",
        "at",
        "b",
        "prid",
    }
)
_NUMBER_RE = re.compile(r"-?\d[\d\s\u00a0\u2009.,]*")
_SPACES = dict.fromkeys(map(ord, " \u00a0\u2009\u202f'"), None)


def host_of(url: str) -> str:
    """Return a lower-cased host without the ``www.`` prefix."""
    return urlsplit(url).netloc.lower().removeprefix("www.")


def clean_url(url: str, *, drop_query: bool = False) -> str:
    """Strip tracking parameters (and optionally the whole query) from *url*."""
    parts = urlsplit(url)
    if drop_query:
        query = ""
    else:
        kept = [
            chunk
            for chunk in parts.query.split("&")
            if chunk
            and (key := chunk.split("=", 1)[0].lower()) not in _TRACKING_PARAMS
            and not key.startswith(_TRACKING_PREFIXES)
        ]
        query = "&".join(kept)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def parse_decimal(value: object) -> Decimal | None:
    """Parse a price out of ``1 299 ₽``, ``"1299.00"``, ``129900`` and friends."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    match = _NUMBER_RE.search(value)
    if match is None:
        return None
    raw = match.group(0).translate(_SPACES).rstrip(".,")
    if raw.count(",") == 1 and raw.count(".") == 0:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def parse_int(value: object) -> int | None:
    """Parse a count out of ``"1 245 отзыва"``, ``"1245"`` or ``1245``."""
    parsed = parse_decimal(value)
    if parsed is None:
        return None
    try:
        return int(parsed)
    except (ValueError, OverflowError):
        return None


def parse_rating(value: object) -> float | None:
    """Parse a 0..5 rating; values outside the range are dropped as noise."""
    parsed = parse_decimal(value)
    if parsed is None:
        return None
    rating = round(float(parsed), 2)
    return rating if 0 <= rating <= 5 else None


def first_str(*values: object) -> str | None:
    """Return the first non-empty string among *values*."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_json_ld(html: str) -> list[dict[str, Any]]:
    """Return every JSON-LD object embedded in *html* (``@graph`` containers unwrapped)."""
    tree = LexborHTMLParser(html)
    found: list[dict[str, Any]] = []
    for node in tree.css('script[type="application/ld+json"]'):
        payload = node.text(strip=True)
        if not payload:
            continue
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            continue
        found.extend(_iter_objects(decoded))
    return found


def _iter_objects(node: object) -> Iterator[dict[str, Any]]:
    if isinstance(node, Mapping):
        mapping = dict(node)
        graph = mapping.get("@graph")
        if isinstance(graph, list):
            yield from _iter_objects(graph)
        yield mapping
    elif isinstance(node, list):
        for item in node:
            yield from _iter_objects(item)


def find_product_entity(entities: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Pick the first ``schema.org/Product`` entity from a JSON-LD collection."""
    for entity in entities:
        raw_type = entity.get("@type")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        if any(isinstance(t, str) and t.lower() == "product" for t in types):
            return dict(entity)
    return None
