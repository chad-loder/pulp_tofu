import pytest

from pulp_tofu.app.protocol import (
    Platform,
    ProviderAddress,
    address_allowed,
    is_provider_part,
    is_provider_type,
    normalize_hostname,
    protocols_supported,
)

AWS = ProviderAddress(hostname="registry.opentofu.org", namespace="hashicorp", type="aws")


@pytest.mark.parametrize(
    ("value", "part", "type_"),
    [
        ("hashicorp", True, True),
        ("google-beta", True, True),
        ("a" * 63, True, True),
        ("a" * 64, False, False),
        ("terraform-aws", True, False),
        ("opentofu-aws", True, False),
        ("-aws", False, False),
        ("aws-", False, False),
        ("a--b", False, False),
        ("Aws", False, False),
        ("a.b", False, False),
        ("", False, False),
    ],
)
def test_provider_parts_follow_opentofu_address_rules(value: str, part: bool, type_: bool) -> None:
    assert (is_provider_part(value), is_provider_type(value)) == (part, type_)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Pulp.Example", "pulp.example"),
        ("pulp.example:443", "pulp.example"),
        ("pulp.example:8443", "pulp.example:8443"),
        ("Bücher.Example", "bücher.example"),
        ("xn--bcher-kva.example", "bücher.example"),
        ("[2001:DB8::1]:8443", "[2001:db8::1]:8443"),
        ("[2001:db8::1]", "[2001:db8::1]"),
    ],
)
def test_hostnames_compare_as_opentofu_compares_them(given: str, expected: str) -> None:
    assert normalize_hostname(given) == expected


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


@pytest.mark.parametrize("key", ["linux", "linux_", "_amd64", "linux_x86_64", "Linux_amd64"])
def test_platform_keys_must_be_go_os_and_arch(key: str) -> None:
    assert Platform.from_key(key) is None
