import hashlib
import io
import time

from pulp_tofu.app.hashes import h1_hash

from .conftest import Mirror
from .fake_registry import Release


def _release(mirror: Mirror, version: str = "1.0.0") -> Release:
    release = mirror.registry.release(mirror.provider_type, version)
    assert release is not None
    return release


def _archive(mirror: Mirror, version: str = "1.0.0") -> tuple[str, bytes]:
    release = _release(mirror, version)
    name = release.filename("linux", "amd64")
    return name, release.archives[name]


def test_pull_through_then_serve_from_pulp(mirror: Mirror) -> None:
    assert mirror.json("index.json") == {"versions": {"1.0.0": {}, "1.1.0": {}}}

    archives = mirror.json("1.0.0.json")["archives"]
    release = _release(mirror)
    expected = {
        f"{os_name}_{arch}": {
            "url": f"1.0.0/{release.filename(os_name, arch)}",
            "hashes": [
                "zh:"
                + hashlib.sha256(release.archives[release.filename(os_name, arch)]).hexdigest()
            ],
        }
        for os_name, arch in (("linux", "amd64"), ("linux", "arm64"), ("darwin", "arm64"))
    }
    assert archives == expected

    name, data = _archive(mirror)
    first = mirror.get(f"1.0.0/{name}")
    assert (first.status, first.complete, first.body == data) == (200, True, True)
    fetched_upstream = mirror.registry.state.hits(f"/{name}")

    second = mirror.get(f"1.0.0/{name}")
    assert second.body == data
    assert mirror.registry.state.hits(f"/{name}") == fetched_upstream

    h1 = h1_hash(io.BytesIO(data))
    hashes: list[str] = []
    for _ in range(60):
        hashes = mirror.json("1.0.0.json")["archives"]["linux_amd64"]["hashes"]
        if h1 in hashes:
            break
        time.sleep(0.5)
    assert h1 in hashes

    version = mirror.pulp.api("GET", mirror.repository)["latest_version_href"]
    summary = mirror.pulp.api("GET", str(version))["content_summary"]
    assert {k: v["count"] for k, v in summary["present"].items()} == {
        "tofu.provider_package": 1,
        "tofu.provider_version": 1,
    }


def test_metadata_is_served_stale_when_upstream_is_down(mirror: Mirror) -> None:
    listing = mirror.json("index.json")
    document = mirror.json("1.0.0.json")
    mirror.update_remote(metadata_ttl=0)
    mirror.registry.state.down = True

    assert mirror.json("index.json") == listing
    assert mirror.json("1.0.0.json") == document
    assert mirror.get("1.1.0.json").status == 502


def test_unknown_provider_is_not_found(mirror: Mirror) -> None:
    mirror.provider_type = "absent"
    assert mirror.get("index.json").status == 404


def test_excluded_provider_never_reaches_upstream(mirror: Mirror) -> None:
    mirror.update_remote(excludes=[f"*/acme/{mirror.provider_type}"])
    assert mirror.get("index.json").status == 404
    assert mirror.get("1.0.0.json").status == 404
    assert mirror.registry.state.requests == []


def test_signature_from_another_key_is_refused(mirror: Mirror) -> None:
    mirror.registry.state.bad_signature = True
    assert mirror.get("1.0.0.json").status == 502
    name, _ = _archive(mirror)
    assert mirror.get(f"1.0.0/{name}").status == 502
    assert mirror.registry.state.hits(f"/{name}") == 0


def test_unsigned_provider_is_refused_outside_opentofu_registry(mirror: Mirror) -> None:
    mirror.registry.state.unsigned = True
    assert mirror.get("1.0.0.json").status == 502


def test_platforms_map_through_signed_sums_without_packages(mirror: Mirror) -> None:
    mirror.registry.state.report_packages = False
    archives = mirror.json("1.0.0.json")["archives"]
    assert sorted(archives) == ["darwin_arm64", "linux_amd64", "linux_arm64"]


def test_corrupt_archive_is_never_stored(mirror: Mirror) -> None:
    name, data = _archive(mirror)
    mirror.registry.state.corrupt_archive = True
    corrupt = mirror.get(f"1.0.0/{name}")
    assert not (corrupt.complete and corrupt.status == 200 and corrupt.body != data)
    assert all(p["artifact"] is None for p in mirror.packages())

    mirror.registry.state.corrupt_archive = False
    clean = mirror.get(f"1.0.0/{name}")
    assert (clean.status, clean.body == data) == (200, True)


def test_upstream_cut_mid_archive_resumes_the_same_stream(mirror: Mirror) -> None:
    name, data = _archive(mirror)
    mirror.registry.state.truncate_archive_after = len(data) - 100_000
    mirror.registry.state.truncate_once = True
    response = mirror.get(f"1.0.0/{name}")
    assert (response.status, response.complete) == (200, True)
    assert hashlib.sha256(response.body).hexdigest() == hashlib.sha256(data).hexdigest()
    assert mirror.registry.state.hits(f"/{name}") == 2


def test_upstream_cut_without_validator_ends_the_stream(mirror: Mirror) -> None:
    name, data = _archive(mirror)
    mirror.registry.state.archive_etag = False
    # Past pulpcore's first 1 MiB read, so bytes have reached the client before the cut.
    mirror.registry.state.truncate_archive_after = len(data) - 100_000
    mirror.registry.state.truncate_once = True
    response = mirror.get(f"1.0.0/{name}")
    assert not response.complete, (len(response.body), len(data))
    assert data.startswith(response.body)
    assert all(p["artifact"] is None for p in mirror.packages())
    assert mirror.get(f"1.0.0/{name}").body == data
