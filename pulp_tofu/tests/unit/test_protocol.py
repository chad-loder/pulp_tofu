import json

import pytest

from pulp_tofu.app.protocol import (
    ProviderAddress,
    address_allowed,
    archive_url,
    index_document,
    protocols_supported,
    version_document,
)

AWS = ProviderAddress(hostname="registry.opentofu.org", namespace="hashicorp", type="aws")


def test_index_document_lists_versions_with_empty_objects() -> None:
    assert json.loads(index_document(["1.0.0", "1.1.0"])) == {
        "versions": {"1.0.0": {}, "1.1.0": {}}
    }


def test_version_document_matches_mirror_protocol_shape() -> None:
    body = version_document(
        {"linux_amd64": {"url": archive_url("1.0.0", "p.zip"), "hashes": ["zh:ab"]}}
    )
    assert json.loads(body) == {
        "archives": {"linux_amd64": {"url": "1.0.0/p.zip", "hashes": ["zh:ab"]}}
    }


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
