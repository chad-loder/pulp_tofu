import base64
import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest
from yarl import URL

from .conftest import Mirror, fetch
from .fake_registry import NAMESPACE

REGISTRY = "/tofu/v1/providers"


@pytest.fixture
def served(
    mirror: Mirror, tofu_sync: Callable[..., Any], tofu_namespace_factory: Callable[..., Any]
) -> Callable[..., Any]:
    """Sync the mirror's provider and put its namespace behind the registry."""

    def serve(**body: Any) -> Any:  # noqa: ANN401
        selection = [{"namespace": NAMESPACE, "type": mirror.provider_type, "latest": 2}]
        tofu_sync(mirror.repository, mirror.remote, selection)
        return tofu_namespace_factory(
            NAMESPACE, mirror.distribution, content_hostname=mirror.hostname, **body
        )

    return serve


def _json(path: str, headers: dict[str, str] | None = None) -> Any:  # noqa: ANN401
    response = fetch(path, headers)
    assert response.status == 200, response
    return json.loads(response.body)


def test_discovery_names_the_providers_service() -> None:
    assert _json("/.well-known/terraform.json") == {"providers.v1": f"{REGISTRY}/"}


def test_registry_serves_versions_and_signed_downloads(
    mirror: Mirror, served: Callable[..., Any]
) -> None:
    served()
    base = f"{REGISTRY}/{NAMESPACE}/{mirror.provider_type}"

    versions = _json(f"{base}/versions")["versions"]
    assert {v["version"] for v in versions} == {"1.0.0", "1.1.0"}
    assert {"os": "linux", "arch": "amd64"} in versions[0]["platforms"]

    document = _json(f"{base}/1.1.0/download/linux/amd64")
    release = mirror.registry.release(mirror.provider_type, "1.1.0")
    assert release is not None
    archive = release.archives[document["filename"]]
    assert document["shasum"] == hashlib.sha256(archive).hexdigest()
    [key] = document["signing_keys"]["gpg_public_keys"]
    assert key["ascii_armor"].strip() == mirror.registry.armored_key.strip()

    # OpenTofu fetches these from the content app; CONTENT_ORIGIN is the e2e proxy.
    def content(url: str) -> bytes:
        response = fetch(URL(url).path)
        assert response.status == 200, (url, response)
        return response.body

    assert content(document["download_url"]) == archive
    assert content(document["shasums_url"]) == release.sums
    assert content(document["shasums_signature_url"]) == release.signature


def test_unknown_namespace_and_provider_are_not_found(
    mirror: Mirror, served: Callable[..., Any]
) -> None:
    served()
    assert fetch(f"{REGISTRY}/nobody/{mirror.provider_type}/versions").status == 404
    assert fetch(f"{REGISTRY}/{NAMESPACE}/missing/versions").status == 404
    base = f"{REGISTRY}/{NAMESPACE}/{mirror.provider_type}"
    assert fetch(f"{base}/9.9.9/download/linux/amd64").status == 404


def test_private_namespace_requires_a_reader(
    mirror: Mirror, served: Callable[..., Any], bindings_cfg: Any
) -> None:
    served(private=True)
    path = f"{REGISTRY}/{NAMESPACE}/{mirror.provider_type}/versions"
    assert fetch(path).status == 401
    login = f"{bindings_cfg.username}:{bindings_cfg.password}".encode()
    admin = {"Authorization": "Basic " + base64.b64encode(login).decode()}
    assert fetch(path, admin).status == 200
