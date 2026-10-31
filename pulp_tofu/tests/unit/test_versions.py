import pytest

from pulp_tofu.app.versions import is_release_version, latest_releases


def test_latest_releases_orders_numerically_and_skips_prereleases() -> None:
    versions = ["1.9.0", "1.10.0", "2.0.0-rc1", "1.10.0+build", "0.9.12", "1.2", "01.0.0"]
    assert latest_releases(versions, 2) == ["1.10.0", "1.9.0"]


@pytest.mark.parametrize(
    ("version", "valid"),
    [
        ("1.2.0", True),
        ("1.2.0-rc.1", True),
        ("1.2.0-beta-2", True),
        ("1.2.0-01", False),
        ("01.2.0", False),
        ("1.2", False),
        ("v1.2.0", False),
        ("1.2.0+build.5", False),
    ],
)
def test_release_versions_are_semver_without_build_metadata(version: str, valid: bool) -> None:
    assert is_release_version(version) is valid
