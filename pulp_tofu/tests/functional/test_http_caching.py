import time

import pytest

from .conftest import Mirror

pytestmark = pytest.mark.parallel


def test_oversized_discovery_document_is_refused(mirror: Mirror) -> None:
    # The limit is 64 KiB; the fake registry pads its discovery document past it.
    mirror.registry.state.discovery_padding = 70 * 1024
    assert mirror.get("index.json").status == 502


def test_stale_metadata_is_revalidated_with_its_etag(mirror: Mirror) -> None:
    # Each content worker keeps its own cache; repeated requests reach a primed one.
    mirror.update_remote(metadata_ttl=1)
    for _ in range(8):
        assert mirror.get("index.json").status == 200
    for _ in range(20):
        time.sleep(1.5)
        assert mirror.get("index.json").status == 200
        if mirror.registry.state.not_modified:
            break
    assert mirror.registry.state.not_modified >= 1, mirror.registry.state.if_none_match
