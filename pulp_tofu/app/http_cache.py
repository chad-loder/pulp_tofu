"""A small in-process HTTP cache for upstream registry metadata.

It follows HTTP caching semantics (RFC 9111) as a shared cache would: ``Cache-Control:
max-age`` or ``Expires`` (less ``Age``) sets freshness; ``no-store`` and ``private``
responses are not kept; ``no-cache`` and responses without freshness are kept but
revalidated each time with ``If-None-Match`` / ``If-Modified-Since``. A 404 is kept as
a negative entry for its ``max-age``, or ``NEGATIVE_TTL`` without one. Callers cap every
lifetime, so an upstream that marks a listing or a 404 fresh for a year cannot hide new
releases. Entries are evicted least recently used, bounded by count and total bytes.

Headers are read case-insensitively and repeated fields are combined: aiohttp renames
``ETag`` to ``Etag``, and some servers split ``Cache-Control`` over several lines.
"""

import time
from collections import OrderedDict
from collections.abc import Hashable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Literal

from pyhaul import EntityTag, parse_etag
from pyhaul.transport import TransportHeaders

NEGATIVE_TTL = 60.0
MAX_ENTRIES = 1024
MAX_TOTAL_BYTES = 32 << 20
MAX_ENTRY_BYTES = 1 << 20


@dataclass(slots=True, kw_only=True)
class CacheEntry:
    """A stored 200 or 404 answer and what is needed to revalidate it."""

    status: Literal[200, 404]
    body: bytes
    headers: TransportHeaders
    fresh_until: float | None

    @property
    def etag(self) -> EntityTag | None:
        return entity_tag(self.headers.get("ETag"))

    def is_fresh(self, now: float) -> bool:
        return self.fresh_until is not None and now < self.fresh_until

    def validators(self) -> dict[str, str]:
        """Conditional request headers for revalidating this entry.

        The ETag goes back exactly as the server sent it: servers compare it as a
        string, and one that sent a bare token would not match pyhaul's quoted form. A
        weak ETag is fine here: ``If-None-Match`` uses weak comparison.
        """
        validators: dict[str, str] = {}
        if self.etag:
            validators["If-None-Match"] = self.headers["ETag"]
        if last_modified := self.headers.get("Last-Modified"):
            validators["If-Modified-Since"] = last_modified
        return validators

    def revalidate(self, headers: TransportHeaders, now: float, *, ceiling: float) -> bool:
        """Apply a 304's headers; False when the 304 confirms some other representation.

        A 304 whose ETag differs from the stored one means the server's 304 is about a
        body this cache does not have (RFC 9111 section 4.3.4); the entry must be
        refetched.
        """
        confirmed = entity_tag(headers.get("ETag"))
        stored = self.etag
        if confirmed and stored and not confirmed.weak_equals(stored):
            return False
        updated = self.headers
        for name in kept_headers(headers):
            updated = updated.without(name) | [(name, value) for value in headers.get_all(name)]
        self.headers = updated
        age = [("Age", value) for value in headers.get_all("Age")]
        _, self.fresh_until = freshness(updated | age, now, default=None, ceiling=ceiling)
        return True


def freshness(
    headers: TransportHeaders, now: float, *, default: float | None, ceiling: float
) -> tuple[bool, float | None]:
    """Whether a response may be stored, and until when it is fresh.

    ``default`` is the lifetime to assume when the response states none; None means
    revalidate every time. No lifetime exceeds ``ceiling`` seconds.
    """
    directives = _cache_control(", ".join(headers.get_all("Cache-Control")))
    if "no-store" in directives or "private" in directives:
        return False, None
    if "no-cache" in directives:
        return True, None
    age = _seconds(headers.get("Age")) or 0.0
    lifetime = _seconds(directives.get("max-age"))
    if lifetime is None:
        lifetime = _expires_in(headers)
    if lifetime is None:
        return True, None if default is None else now + min(default, ceiling)
    return True, now + min(max(lifetime - age, 0.0), ceiling)


class HttpCache:
    """LRU store of ``CacheEntry`` by key, bounded by entry count and total body bytes."""

    def __init__(
        self,
        *,
        max_entries: int = MAX_ENTRIES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        max_entry_bytes: int = MAX_ENTRY_BYTES,
    ) -> None:
        self._entries: OrderedDict[Hashable, CacheEntry] = OrderedDict()
        self._bytes = 0
        self._max_entries = max_entries
        self._max_total_bytes = max_total_bytes
        self._max_entry_bytes = max_entry_bytes

    def get(self, key: Hashable) -> CacheEntry | None:
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry

    def put(self, key: Hashable, entry: CacheEntry) -> None:
        """Store ``entry``; one larger than the per-entry limit is not kept."""
        self.discard(key)
        if len(entry.body) > self._max_entry_bytes:
            return
        self._entries[key] = entry
        self._bytes += len(entry.body)
        while len(self._entries) > self._max_entries or self._bytes > self._max_total_bytes:
            _, evicted = self._entries.popitem(last=False)
            self._bytes -= len(evicted.body)

    def discard(self, key: Hashable) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= len(entry.body)


def kept_headers(headers: TransportHeaders) -> TransportHeaders:
    """The fields an entry needs to revalidate and to recompute its freshness.

    ``Age`` is left out: it describes the response as received, and a later 304
    carries its own.
    """
    return TransportHeaders(pair for pair in headers.multi_items() if pair[0] in _KEPT)


_KEPT = frozenset({"etag", "last-modified", "cache-control", "expires", "date"})


def monotonic() -> float:
    return time.monotonic()


def entity_tag(value: str | None) -> EntityTag | None:
    """A response's ``ETag``, or None when absent or malformed."""
    parsed = parse_etag(value) if value else None
    return parsed if parsed else None


def _cache_control(value: str) -> dict[str, str | None]:
    directives: dict[str, str | None] = {}
    for part in value.split(","):
        name, _, argument = part.strip().partition("=")
        if name:
            directives[name.lower()] = argument.strip('"') or None
    return directives


def _seconds(value: str | None) -> float | None:
    if value is None or not value.strip().isdigit():
        return None
    return float(value.strip())


def _expires_in(headers: TransportHeaders) -> float | None:
    """Seconds until ``Expires``, measured against the response's ``Date`` when present."""
    expires = _http_date(headers.get("Expires"))
    if expires is None:
        return None if "Expires" not in headers else 0.0
    date = _http_date(headers.get("Date"))
    if date is None:
        return max(expires - time.time(), 0.0)
    return max(expires - date, 0.0)


def _http_date(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
