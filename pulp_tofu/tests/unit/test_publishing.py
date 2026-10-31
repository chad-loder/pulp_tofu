import json

import pytest

from pulp_tofu.app.protocol import Platform
from pulp_tofu.app.publishing import ReleaseError, manifest_protocols, parse_release, sha256sums

PREFIX = "terraform-provider-widget_1.2.0"
RELEASE = [
    f"{PREFIX}_linux_amd64.zip",
    f"{PREFIX}_darwin_arm64.zip",
    f"{PREFIX}_SHA256SUMS",
    f"{PREFIX}_SHA256SUMS.sig",
    f"{PREFIX}_manifest.json",
]


def test_release_files_are_sorted_by_role() -> None:
    release = parse_release(RELEASE)
    assert (release.provider_type, release.version) == ("widget", "1.2.0")
    assert release.shasums == f"{PREFIX}_SHA256SUMS"
    assert release.signature == f"{PREFIX}_SHA256SUMS.sig"
    assert release.manifest == f"{PREFIX}_manifest.json"
    assert release.archives == {
        Platform(os="linux", arch="amd64"): f"{PREFIX}_linux_amd64.zip",
        Platform(os="darwin", arch="arm64"): f"{PREFIX}_darwin_arm64.zip",
    }


@pytest.mark.parametrize(
    "files",
    [
        RELEASE[:2] + RELEASE[3:],
        RELEASE[2:],
        [*RELEASE, RELEASE[0]],
        [*RELEASE, "terraform-provider-widget_1.3.0_linux_arm64.zip"],
        [*RELEASE, "terraform-provider-other_1.2.0_linux_arm64.zip"],
        [*RELEASE, f"{PREFIX}_linux_amd64.tar.gz"],
        [*RELEASE, "../terraform-provider-widget_1.2.0_linux_arm64.zip"],
        [*RELEASE, f"{PREFIX}_Linux_amd64.zip"],
        [n.replace("widget", "terraform-widget") for n in RELEASE],
        [n.replace("widget", "widget-") for n in RELEASE],
        [n.replace("widget", "wid--get") for n in RELEASE],
        [n.replace("1.2.0", "1.2") for n in RELEASE],
        [n.replace("1.2.0", "01.2.0") for n in RELEASE],
        [n.replace("1.2.0", "1.2.0+build") for n in RELEASE],
    ],
    ids=[
        "signature without sums",
        "no archives",
        "repeated file",
        "two versions",
        "two types",
        "not a zip",
        "path",
        "uppercase platform",
        "redundant type prefix",
        "trailing dash",
        "double dash",
        "partial version",
        "leading zero",
        "build metadata",
    ],
)
def test_incomplete_or_mixed_releases_are_refused(files: list[str]) -> None:
    with pytest.raises(ReleaseError):
        parse_release(files)


def test_unsigned_releases_leave_signing_to_pulp() -> None:
    unsigned = parse_release(RELEASE[:3])
    assert (unsigned.shasums, unsigned.signature) == (f"{PREFIX}_SHA256SUMS", None)
    bare = parse_release(RELEASE[:2])
    assert (bare.shasums, bare.signature) == (None, None)


def test_generated_sums_follow_goreleaser_layout() -> None:
    digests = {f"{PREFIX}_linux_amd64.zip": "b" * 64, f"{PREFIX}_darwin_arm64.zip": "a" * 64}
    assert (
        sha256sums(digests)
        == (
            f"{'a' * 64}  {PREFIX}_darwin_arm64.zip\n{'b' * 64}  {PREFIX}_linux_amd64.zip\n"
        ).encode()
    )


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (None, ("5.0",)),
        ({"version": 1, "metadata": {}}, ("5.0",)),
        ({"version": 1, "metadata": {"protocol_versions": ["6.0"]}}, ("6.0",)),
    ],
)
def test_manifest_declares_protocols(manifest: object, expected: tuple[str, ...]) -> None:
    document = None if manifest is None else json.dumps(manifest).encode()
    assert manifest_protocols(document) == expected


@pytest.mark.parametrize(
    "document",
    [
        b"not json",
        json.dumps({"metadata": {"protocol_versions": "6.0"}}).encode(),
        json.dumps({"metadata": {"protocol_versions": []}}).encode(),
        json.dumps({"metadata": {"protocol_versions": ["4.0"]}}).encode(),
    ],
)
def test_unusable_manifests_are_refused(document: bytes) -> None:
    with pytest.raises(ReleaseError):
        manifest_protocols(document)
