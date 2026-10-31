"""When and how soon to retry an upstream request.

One policy for registry metadata and archive downloads: retry 429, 5xx and transport
failures; wait what ``Retry-After`` asks, and give up when it asks for more than
``MAX_RETRY_AFTER``, rather than return sooner than the server allows; otherwise back
off exponentially with full jitter, so clients that failed together do not return
together.
"""

import random

from aiohttp import ClientConnectionError, ClientPayloadError, ClientResponseError
from pyhaul import UnexpectedStatusError
from pyhaul.transport import TransportHeaders

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


def retry_delay(failures: int, exc: BaseException) -> float | None:
    """Seconds to wait after ``failures`` consecutive failures, the last being ``exc``.

    None means do not retry: the server asked for a longer wait than a waiting client
    should sit through.
    """
    if isinstance(exc, ClientResponseError) and exc.headers is not None:
        # pyhaul parses Retry-After only through its status error.
        headers = TransportHeaders.from_pairs(exc.headers.items())
        asked = UnexpectedStatusError(exc.status, headers).retry_after_seconds
        if asked is not None:
            return asked if asked <= MAX_RETRY_AFTER else None
    ceiling = min(MAX_DELAY, BASE_DELAY * 2 ** max(failures - 1, 0))
    return random.uniform(0, ceiling)
