import pytest
from pyhaul.transport import TransportHeaders

from pulp_tofu.app.http_cache import CacheEntry, HttpCache, freshness, kept_headers

NOW = 1000.0
DAY = 86400.0


def _entry(body: bytes = b"{}", **headers: str) -> CacheEntry:
    return CacheEntry(
        status=200,
        body=body,
        headers=kept_headers(TransportHeaders.build(**headers)),
        fresh_until=None,
    )


@pytest.mark.parametrize(
    ("pairs", "default", "expected"),
    [
        ([("Cache-Control", "max-age=60")], None, (True, NOW + 60)),
        ([("Cache-Control", "max-age=60"), ("Age", "50")], None, (True, NOW + 10)),
        ([("Cache-Control", "max-age=60"), ("Age", "90")], None, (True, NOW)),
        ([("Cache-Control", "max-age=31536000")], None, (True, NOW + DAY)),
        ([("Cache-Control", "no-store, max-age=60")], None, (False, None)),
        ([("Cache-Control", 'no-cache="Set-Cookie, Vary", max-age=60')], 60.0, (True, None)),
        ([("cache-control", "max-age=60"), ("Cache-Control", "private")], None, (False, None)),
        ([("Cache-Control", "no-cache")], 60.0, (True, None)),
        ([("Expires", "Thu, 01 Jan 1970 00:00:00 GMT")], None, (True, NOW)),
        ([("Expires", "not a date")], None, (True, NOW)),
        (
            [
                ("Date", "Sat, 31 Oct 2026 10:00:00 GMT"),
                ("Expires", "Sat, 31 Oct 2026 10:02:00 GMT"),
            ],
            None,
            (True, NOW + 120),
        ),
        ([], None, (True, None)),
        ([], 60.0, (True, NOW + 60)),
    ],
)
def test_freshness_follows_http_caching(
    pairs: list[tuple[str, str]], default: float | None, expected: tuple[bool, float | None]
) -> None:
    headers = TransportHeaders.from_pairs(pairs)
    assert freshness(headers, NOW, default=default, ceiling=DAY) == expected


def test_validators_echo_the_server_etag_whatever_its_case_or_quoting() -> None:
    entry = _entry(Etag="abc123", last_modified="Sat, 31 Oct 2026 10:00:00 GMT")
    assert entry.validators() == {
        "If-None-Match": "abc123",
        "If-Modified-Since": "Sat, 31 Oct 2026 10:00:00 GMT",
    }
    assert _entry(ETag='"unterminated').validators() == {}


def test_revalidation_keeps_stored_lifetime_and_rejects_another_etag() -> None:
    entry = _entry(ETag='"v1"', cache_control="max-age=60")
    assert entry.revalidate(TransportHeaders.build(Age="20"), NOW, ceiling=DAY)
    assert entry.fresh_until == NOW + 40
    assert entry.revalidate(TransportHeaders.build(ETag='W/"v1"'), NOW, ceiling=DAY)
    assert not entry.revalidate(TransportHeaders.build(ETag='"v2"'), NOW, ceiling=DAY)


def test_cache_evicts_least_recently_used_within_byte_budget() -> None:
    cache = HttpCache(max_entries=10, max_total_bytes=10, max_entry_bytes=6)
    cache.put("a", _entry(b"x" * 4))
    cache.put("b", _entry(b"x" * 4))
    assert cache.get("a") is not None
    cache.put("c", _entry(b"x" * 4))
    assert cache.get("b") is None
    assert cache.get("a") is not None
    cache.put("big", _entry(b"x" * 7))
    assert cache.get("big") is None
