import base64
import hashlib
import json
import secrets
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest
from yarl import URL

from pulpcore.client.pulp_tofu.exceptions import ApiException
from pulpcore.tests.functional.utils import PulpTaskError

from pulp_tofu.app.signing import verify_sha256sums

from .conftest import PULP_URL, Publisher, fetch, module_package, release_files, tls_context
from .fake_registry import FakeRegistry, Release, signed_release

pytestmark = pytest.mark.parallel


def test_published_release_is_served_by_the_registry(
    publisher: Publisher, release: Release, provider_type: str
) -> None:
    publisher.publish(release_files(provider_type, "1.0.0", release))

    base = f"{publisher.providers}/{publisher.namespace.name}/{provider_type}"
    versions = json.loads(fetch(f"{base}/versions").body)["versions"]
    assert [(v["version"], v["protocols"]) for v in versions] == [("1.0.0", ["6.0"])]
    document = json.loads(fetch(f"{base}/1.0.0/download/linux/amd64").body)
    archive = release.archives[document["filename"]]
    assert document["shasum"] == hashlib.sha256(archive).hexdigest()
    assert fetch(URL(document["download_url"]).path).body == archive
    hashes = document["packages"]["linux_amd64"]["hashes"]
    assert any(h.startswith("h1:") for h in hashes)
    mixed = (
        f"{publisher.providers}/{publisher.namespace.name.upper()}/{provider_type.title()}/versions"
    )
    assert json.loads(fetch(mixed).body)["versions"] == versions

    # Publishing the same release again changes nothing.
    publisher.publish(release_files(provider_type, "1.0.0", release))
    assert len(json.loads(fetch(f"{base}/versions").body)["versions"]) == 1


def test_a_published_version_cannot_change(
    publisher: Publisher, registry: FakeRegistry, release: Release, provider_type: str
) -> None:
    publisher.publish(release_files(provider_type, "1.0.0", release))

    [name, *_] = release.archives
    changed = signed_release(
        registry.key, {**release.archives, name: release.archives[name] + b"\0"}
    )
    with pytest.raises(ApiException) as error:
        publisher.publish(release_files(provider_type, "1.0.0", changed))
    assert error.value.status == 409


def test_release_signed_by_another_key_is_refused(
    publisher: Publisher, registry: FakeRegistry, release: Release, provider_type: str
) -> None:
    files = release_files(
        provider_type, "1.0.0", signed_release(registry.other_key, release.archives)
    )
    with pytest.raises(PulpTaskError) as error:
        publisher.publish(files)
    assert "does not verify" in error.value.task.error["description"]
    base = f"{publisher.providers}/{publisher.namespace.name}/{provider_type}"
    assert fetch(f"{base}/versions").status == 404


def test_archives_must_match_the_signed_checksums(
    publisher: Publisher, release: Release, provider_type: str
) -> None:
    [name, *_] = release.archives
    tampered = Release({**release.archives, name: b"tampered"}, release.sums, release.signature)
    with pytest.raises(PulpTaskError) as error:
        publisher.publish(release_files(provider_type, "1.0.0", tampered))
    assert "does not match its SHA256SUMS digest" in error.value.task.error["description"]


def test_a_distribution_with_a_remote_refuses_uploads(
    tofu_distribution_factory: Callable[..., Any], tofu_remote_factory: Callable[..., Any]
) -> None:
    remote = tofu_remote_factory(url="https://registry.example/")
    with pytest.raises(ApiException) as error:
        tofu_distribution_factory(remote=remote, allow_uploads=True)
    assert error.value.status == 400


def test_release_job_publishes_with_a_publish_token(
    publisher: Publisher,
    release: Release,
    provider_type: str,
    tofu_bindings: Any,
    gen_object_with_cleanup: Callable[..., Any],
) -> None:
    files = release_files(provider_type, "1.0.0", release)

    def token(scope: str) -> str:
        body = {"name": scope, "scope": scope, "namespaces": [publisher.namespace.pulp_href]}
        return str(gen_object_with_cleanup(tofu_bindings.PulpTofuTokensApi, body).token)

    with pytest.raises(ApiException) as error:
        publisher.publish(files, token=token("read"))
    assert error.value.status == 403
    publisher.publish(files, token=token("publish"))
    base = f"{publisher.providers}/{publisher.namespace.name}/{provider_type}"
    assert fetch(f"{base}/versions").status == 200


def test_a_token_publishes_only_what_its_user_may(
    publisher: Publisher,
    release: Release,
    provider_type: str,
    tofu_bindings: Any,
    gen_user: Callable[..., Any],
) -> None:
    reader = gen_user(object_roles=[("tofu.tofunamespace_consumer", publisher.namespace.pulp_href)])
    body = {"name": "ci", "scope": "publish", "namespaces": [publisher.namespace.pulp_href]}
    with reader:
        token = tofu_bindings.PulpTofuTokensApi.create(body)
    with pytest.raises(ApiException) as error:
        publisher.publish(release_files(provider_type, "1.0.0", release), token=str(token.token))
    assert error.value.status == 403


def test_update_token_publishes_only_versions_of_existing_providers(
    publisher: Publisher,
    registry: FakeRegistry,
    release: Release,
    provider_type: str,
    tofu_bindings: Any,
    gen_object_with_cleanup: Callable[..., Any],
) -> None:
    body = {"name": "ci", "scope": "update", "namespaces": [publisher.namespace.pulp_href]}
    update = str(gen_object_with_cleanup(tofu_bindings.PulpTofuTokensApi, body).token)
    first = release_files(provider_type, "1.0.0", release)
    with pytest.raises(ApiException) as error:
        publisher.publish(first, token=update)
    assert error.value.status == 403

    publisher.publish(first)
    later = registry.release(provider_type, "1.1.0")
    assert later is not None
    publisher.publish(release_files(provider_type, "1.1.0", later), token=update)
    versions = json.loads(
        fetch(f"{publisher.providers}/{publisher.namespace.name}/{provider_type}/versions").body
    )
    assert {v["version"] for v in versions["versions"]} == {"1.0.0", "1.1.0"}


def test_published_module_is_served_by_the_registry(publisher: Publisher) -> None:
    package = module_package(("main.tf", b'variable "cidr" {}\n'))
    publisher.publish_module("1.0.0", package)
    publisher.publish_module("1.0.0", package)

    base = f"{publisher.modules}/{publisher.namespace.name}/vpc/aws"
    listing = json.loads(fetch(f"{base}/versions").body)
    assert listing == {"modules": [{"versions": [{"version": "1.0.0"}]}]}
    location = json.loads(fetch(f"{base}/1.0.0/download").body)["location"]
    assert location.endswith("/vpc/aws/1.0.0.tar.gz")
    assert fetch(URL(location).raw_path_qs).body == package
    assert fetch(f"{base}/9.9.9/download").status == 404


def test_a_published_module_version_cannot_change(publisher: Publisher) -> None:
    publisher.publish_module("1.0.0", module_package(("main.tf", b"# one\n")))
    with pytest.raises(ApiException) as error:
        publisher.publish_module("1.0.0", module_package(("main.tf", b"# two\n")))
    assert error.value.status == 409


def test_module_names_ignore_case(publisher: Publisher) -> None:
    publisher.publish_module("1.0.0", module_package(("main.tf", b"# vpc\n")), name="Vpc")

    namespace = publisher.namespace.name
    for path in (f"{namespace}/Vpc/aws", f"{namespace.upper()}/vpc/AWS"):
        listing = json.loads(fetch(f"{publisher.modules}/{path}/versions").body)
        assert listing == {"modules": [{"versions": [{"version": "1.0.0"}]}]}
    with pytest.raises(ApiException) as error:
        publisher.publish_module("1.1.0", module_package(("main.tf", b"# lookalike\n")), name="vpc")
    assert error.value.status == 409
    assert "differs only in case" in str(error.value.body)


def test_module_package_escaping_its_directory_is_refused(publisher: Publisher) -> None:
    with pytest.raises(PulpTaskError) as error:
        publisher.publish_module("1.0.0", module_package(("../main.tf", b"# escape\n")))
    assert "leaves the module directory" in error.value.task.error["description"]


def test_unsigned_release_is_signed_by_the_namespace_signing_service(
    publisher: Publisher,
    release: Release,
    provider_type: str,
    tofu_signing_service: Any,
    tofu_namespace_factory: Callable[..., Any],
) -> None:
    service = tofu_signing_service
    publisher.namespace = tofu_namespace_factory(
        f"signed{secrets.token_hex(4)}",
        publisher.distribution,
        signing_service=service.pulp_href,
    )
    publisher.publish(list(release.archives.items()))

    base = f"{publisher.providers}/{publisher.namespace.name}/{provider_type}"
    document = json.loads(fetch(f"{base}/1.0.0/download/linux/amd64").body)
    [key] = document["signing_keys"]["gpg_public_keys"]
    assert key["key_id"] == service.pubkey_fingerprint[-16:].upper()
    sums = fetch(URL(document["shasums_url"]).path).body
    signature = fetch(URL(document["shasums_signature_url"]).path).body
    signer = verify_sha256sums(sums, signature, [service.public_key])
    assert signer.fingerprint == service.pubkey_fingerprint.lower()
    assert hashlib.sha256(release.archives[document["filename"]]).hexdigest() in sums.decode()


def test_release_published_from_uploaded_artifacts(
    publisher: Publisher,
    release: Release,
    provider_type: str,
    pulpcore_bindings: Any,
    bindings_cfg: Any,
) -> None:
    def artifact(name: str, data: bytes) -> str:
        # As pulp-glue uploads: reuse an artifact Pulp already has.
        found = pulpcore_bindings.ArtifactsApi.list(sha256=hashlib.sha256(data).hexdigest())
        if found.results:
            return str(found.results[0].pulp_href)
        return str(pulpcore_bindings.ArtifactsApi.create(file=(name, data)).pulp_href)

    hrefs = {
        name: artifact(name, data) for name, data in release_files(provider_type, "1.0.0", release)
    }
    login = f"{bindings_cfg.username}:{bindings_cfg.password}".encode()
    request = urllib.request.Request(
        f"{PULP_URL}{publisher.namespace.pulp_href}publish/",
        data=json.dumps({"artifacts": hrefs}).encode(),
        headers={
            "Authorization": "Basic " + base64.b64encode(login).decode(),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, context=tls_context(), timeout=60) as response:
        publisher.monitor_task(json.loads(response.read())["task"])
    base = f"{publisher.providers}/{publisher.namespace.name}/{provider_type}"
    assert fetch(f"{base}/versions").status == 200


def test_unsigned_release_without_a_signing_service_is_refused(
    publisher: Publisher, release: Release
) -> None:
    with pytest.raises(ApiException) as error:
        publisher.publish(list(release.archives.items()))
    assert error.value.status == 400
