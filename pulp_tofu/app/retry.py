"""When and how soon to retry an upstream request.

One policy for registry metadata and archive downloads: retry 429, 5xx and transport
failures; wait what ``Retry-After`` asks, within ``MAX_RETRY_AFTER``; otherwise back
off exponentially with full jitter, so clients that failed together do not return
together.
"""

import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from aiohttp import ClientConnectionError, ClientPayloadError, ClientResponseError

from pulpcore.plugin.exceptions import SizeValidationError, TimeoutException

# A busy registry may ask for longer, but a client is waiting on this request.
MAX_RETRY_AFTER = 30.0
BASE_DELAY = 0.5
MAX_DELAY = 10.0


def is_retryable(exc: BaseException) -> bool:
    """Whether a failed upstream request is worth repeating."""
    if isinstance(exc, ClientResponseError):
        return exc.status == 429 or 500 <= exc.status < 600
    return isinstance(
        exc,
        ClientConnectionError
        | ClientPayloadError
        | OSError
        | TimeoutError
        | TimeoutException
        | SizeValidationError,
    )


def retry_delay(failures: int, exc: BaseException) -> float:
    """Seconds to wait after ``failures`` consecutive failures, the last being ``exc``."""
    if isinstance(exc, ClientResponseError) and exc.headers is not None:
        asked = _retry_after(exc.headers.get("Retry-After"))
        if asked is not None:
            return min(asked, MAX_RETRY_AFTER)
    ceiling = min(MAX_DELAY, BASE_DELAY * 2 ** max(failures - 1, 0))
    return random.uniform(0, ceiling)


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    if value.strip().isdigit():
        return float(value.strip())
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)
