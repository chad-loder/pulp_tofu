import pytest

from pulp_tofu.app.protocol import ProviderAddress, address_allowed, protocols_supported

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
