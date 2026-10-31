import json
from pathlib import Path

import pytest
from yarl import URL

from pulp_tofu.app.protocol import Platform
from pulp_tofu.app.registry import (
    UpstreamError,
    parse_discovery,
    parse_download,
    parse_versions,
)

RANDOM = Path(__file__).parent / "fixtures" / "random_3.7.2"
DOWNLOAD_URL = URL(
    "https://registry.opentofu.org/v1/providers/hashicorp/random/3.7.2/download/linux/amd64"
)


def test_discovery_resolves_relative_service() -> None:
    base = URL("https://registry.opentofu.org/.well-known/terraform.json")
    resolved = parse_discovery({"providers.v1": "/v1/providers/"}, base)
    assert str(resolved) == "https://registry.opentofu.org/v1/providers/"


def test_discovery_requires_providers_service() -> None:
    with pytest.raises(UpstreamError, match="providers.v1"):
        parse_discovery({"modules.v1": "/v1/modules/"}, URL("https://r.example/"))


def test_discovery_refuses_plain_http() -> None:
    with pytest.raises(UpstreamError, match="https"):
        parse_discovery({"providers.v1": "http://r.example/v1/"}, URL("https://r.example/"))


def test_versions_parse_platforms_and_skip_bad_entries() -> None:
    parsed = parse_versions(
        {
            "versions": [
                {
                    "version": "1.0.0",
                    "protocols": ["5.0"],
                    "platforms": [{"os": "linux", "arch": "amd64"}, {"os": ""}],
                },
                {"version": "../1.0.0"},
                {"version": 3},
            ]
        }
    )
    assert len(parsed) == 1
    assert parsed[0].version == "1.0.0"
    assert parsed[0].protocols == ("5.0",)
    assert parsed[0].platforms == (Platform(os="linux", arch="amd64"),)


def test_download_parses_published_document() -> None:
    info = parse_download(json.loads((RANDOM / "dl.json").read_text()), DOWNLOAD_URL)
    assert info.platform == Platform(os="linux", arch="amd64")
    assert info.filename == "terraform-provider-random_3.7.2_linux_amd64.zip"
    assert info.shasum == "9b0ac4c1d8e36a86b59ced94fa517ae9b015b1d044b3455465cc6f0eab70915d"
    assert info.shasums_url.endswith("_SHA256SUMS")
    assert len(info.signing_keys) == 1
    assert info.packages["linux_amd64"].zh == info.shasum
    assert info.packages["linux_amd64"].size == 5769353


def _download(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "os": "linux",
        "arch": "amd64",
        "filename": "p.zip",
        "download_url": "p.zip",
        "shasums_url": "SHA256SUMS",
        "shasums_signature_url": "SHA256SUMS.sig",
        "shasum": "a" * 64,
    }
    document.update(overrides)
    return document


def test_download_resolves_relative_urls() -> None:
    info = parse_download(_download(), DOWNLOAD_URL)
    assert info.download_url == str(DOWNLOAD_URL.parent / "p.zip")
    assert info.signing_keys == ()
    assert info.packages == {}


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"filename": "../p.zip"}, "single path segment"),
        ({"shasum": "xyz"}, "SHA-256"),
        ({"download_url": "http://evil.example/p.zip"}, "https"),
        ({"arch": None}, "os and arch"),
    ],
)
def test_download_rejects_bad_documents(override: dict[str, object], message: str) -> None:
    with pytest.raises(UpstreamError, match=message):
        parse_download(_download(**override), DOWNLOAD_URL)
