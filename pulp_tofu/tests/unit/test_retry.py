from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
from aiohttp import ClientResponseError, RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from pulp_tofu.app.retry import MAX_DELAY, retry_delay


def _answer(status: int, retry_after: str | None = None) -> ClientResponseError:
    url = URL("https://registry.example/v1/providers/acme/widget/versions")
    headers = (
        CIMultiDict[str]() if retry_after is None else CIMultiDict({"Retry-After": retry_after})
    )
    info = RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict[str]()), url)
    return ClientResponseError(info, (), status=status, headers=CIMultiDictProxy(headers))


def test_retry_after_seconds_are_waited_exactly() -> None:
    assert retry_delay(1, _answer(429, "3")) == 3.0


def test_retry_after_date_is_waited_until() -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=20), usegmt=True)
    delay = retry_delay(1, _answer(503, when))
    assert delay is not None and 18.0 <= delay <= 20.0


def test_retry_after_beyond_the_limit_gives_up() -> None:
    assert retry_delay(1, _answer(429, "120")) is None


@pytest.mark.parametrize("retry_after", [None, "soon"])
def test_without_a_usable_retry_after_backoff_is_jittered_and_capped(
    retry_after: str | None,
) -> None:
    delays = [retry_delay(10, _answer(503, retry_after)) for _ in range(50)]
    assert all(d is not None and 0.0 <= d <= MAX_DELAY for d in delays)
    assert len(set(delays)) > 1
