from collections.abc import Callable
from typing import Any

import pytest

from .conftest import Mirror
from .fake_registry import NAMESPACE

pytestmark = pytest.mark.parallel


def _selection(mirror: Mirror, **extra: object) -> list[dict[str, object]]:
    return [{"namespace": NAMESPACE, "type": mirror.provider_type, **extra}]


def test_on_demand_sync_records_latest_release_without_downloading(
    mirror: Mirror, tofu_sync: Callable[..., Any]
) -> None:
    tofu_sync(mirror.repository, mirror.remote, _selection(mirror))

    assert mirror.present_content() == {
        "tofu.provider_package": 3,
        "tofu.provider_version": 1,
    }
    assert mirror.registry.state.hits(".zip") == 0
    assert {p.version for p in mirror.packages()} == {"1.1.0"}
    assert all(p.artifact is None for p in mirror.packages())


def test_immediate_sync_downloads_selected_platforms_and_hashes_them(
    mirror: Mirror, tofu_sync: Callable[..., Any]
) -> None:
    mirror.update_remote(policy="immediate")
    tofu_sync(
        mirror.repository,
        mirror.remote,
        _selection(mirror, latest=2),
        platforms=["linux_amd64"],
    )

    packages = mirror.packages()
    assert sorted((p.version, p.os, p.arch) for p in packages) == [
        ("1.0.0", "linux", "amd64"),
        ("1.1.0", "linux", "amd64"),
    ]
    assert all(p.artifact is not None and p.h1.startswith("h1:") for p in packages)
    hashes = mirror.json("1.1.0.json")["archives"]["linux_amd64"]["hashes"]
    assert packages[0].h1 in hashes or packages[1].h1 in hashes


def test_synced_repository_serves_without_upstream_metadata(
    mirror: Mirror, tofu_sync: Callable[..., Any]
) -> None:
    mirror.update_remote(policy="immediate")
    tofu_sync(mirror.repository, mirror.remote, _selection(mirror), platforms=["linux_amd64"])
    mirror.registry.state.down = True

    assert mirror.json("index.json")["versions"].keys() >= {"1.1.0"}
    release = mirror.registry.release(mirror.provider_type, "1.1.0")
    assert release is not None
    name = release.filename("linux", "amd64")
    response = mirror.get(f"1.1.0/{name}")
    assert (response.status, response.body == release.archives[name]) == (200, True)
