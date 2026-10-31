import pytest

from pulp_tofu.app.protocol import (
    Platform,
    ProviderAddress,
    address_allowed,
    latest_releases,
    protocols_supported,
)

AWS = ProviderAddress(hostname="registry.opentofu.org", namespace="hashicorp", type="aws")


@pytest.mark.parametrize(
    ("protocols", "supported"),
    [((), True), (("5.0",), True), (("6.0", "4.0"), True), (("4.0",), False), (("7.0",), False)],
)
def test_protocols_supported(protocols: tuple[str, ...], supported: bool) -> None:
    assert protocols_supported(protocols) is supported


@pytest.mark.parametrize(
    ("includes", "excludes", "allowed"),
    [
        ([], [], True),
        (["registry.opentofu.org/hashicorp/*"], [], True),
        (["registry.opentofu.org/other/*"], [], False),
        ([], ["*/hashicorp/aws"], False),
        (["registry.opentofu.org/*/*"], ["*/*/aws"], False),
    ],
)
def test_address_allowed(includes: list[str], excludes: list[str], allowed: bool) -> None:
    assert address_allowed(AWS, includes, excludes) is allowed


def test_latest_releases_orders_numerically_and_skips_prereleases() -> None:
    versions = ["1.9.0", "1.10.0", "2.0.0-rc1", "1.10.0+build", "0.9.12", "1.2"]
    assert latest_releases(versions, 2) == ["1.10.0", "1.9.0"]


@pytest.mark.parametrize("key", ["linux", "linux_", "_amd64", "linux_x86_64", "Linux_amd64"])
def test_platform_keys_must_be_go_os_and_arch(key: str) -> None:
    assert Platform.from_key(key) is None
