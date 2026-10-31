import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pysequoia import Tsk

from .conftest import Mirror

pytestmark = pytest.mark.parallel


def test_stale_listing_is_served_at_once_and_refreshed_behind(mirror: Mirror) -> None:
    mirror.update_remote(metadata_ttl=1)
    assert set(mirror.json("index.json")["versions"]) == {"1.0.0", "1.1.0"}
    mirror.registry.providers[mirror.provider_type].append("1.2.0")
    time.sleep(1.5)

    started = time.monotonic()
    stale = mirror.json("index.json")
    assert time.monotonic() - started < 1.0
    assert "1.2.0" not in stale["versions"]

    for _ in range(20):
        if "1.2.0" in mirror.json("index.json")["versions"]:
            break
        time.sleep(0.25)
    else:
        raise AssertionError("the background refresh never landed")


def test_unknown_provider_lookups_are_cached(mirror: Mirror) -> None:
    mirror.provider_type = "nosuchprovider"
    assert mirror.get("index.json").status == 404
    assert mirror.get("index.json").status == 404
    assert mirror.registry.state.hits("/nosuchprovider/versions") == 1


def test_concurrent_first_requests_share_one_download(mirror: Mirror) -> None:
    release = mirror.registry.release(mirror.provider_type, "1.0.0")
    assert release is not None
    name = release.filename("linux", "amd64")
    assert mirror.get("1.0.0.json").status == 200
    mirror.registry.state.archive_delay = 1.0
    clients = 8
    with ThreadPoolExecutor(clients) as pool:
        responses = list(pool.map(lambda _: mirror.get(f"1.0.0/{name}"), range(clients)))

    assert all(r.status == 200 and r.body == release.archives[name] for r in responses)
    # One download per content-app worker at most; without coalescing it would be one each.
    assert mirror.registry.state.hits(f"/{name}") < clients


def test_throttled_requests_wait_what_retry_after_asks(mirror: Mirror) -> None:
    state = mirror.registry.state
    state.throttle = 2
    assert mirror.get("index.json").status == 200
    # Both 429s are answered after their Retry-After of 1 second, not sooner.
    assert state.times[1] - state.times[0] >= 1.0
    assert state.times[2] - state.times[1] >= 1.0

    release = mirror.registry.release(mirror.provider_type, "1.0.0")
    assert release is not None
    name = release.filename("linux", "amd64")
    assert mirror.get("1.0.0.json").status == 200
    state.throttle = 1
    response = mirror.get(f"1.0.0/{name}")
    assert (response.status, response.body == release.archives[name]) == (200, True)


def test_retry_after_longer_than_a_client_waits_gives_up(mirror: Mirror) -> None:
    state = mirror.registry.state
    state.throttle, state.retry_after = 5, "120"
    started = time.monotonic()
    assert mirror.get("index.json").status == 502
    assert time.monotonic() - started < 10
    assert state.throttle == 4


def test_trusted_signing_keys_refuse_other_signers(mirror: Mirror) -> None:
    impostor = Tsk.generate("impostor@example.com").extract_certificate().fingerprint
    mirror.update_remote(signing_keys=[impostor])
    assert mirror.get("1.0.0.json").status == 502

    trusted = mirror.registry.key.extract_certificate().fingerprint
    mirror.update_remote(signing_keys=[trusted])
    assert mirror.get("1.0.0.json").status == 200
