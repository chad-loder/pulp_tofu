import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from yarl import URL

from pulpcore.client.pulp_tofu import ApiClient, Configuration, PulpTofuNamespacesApi
from pulpcore.client.pulp_tofu.exceptions import ApiException
from pulpcore.tests.functional.utils import PulpTaskError

from .conftest import E2E, PULP_URL, fetch
from .fake_registry import FakeRegistry, Release, signed_release

REGISTRY = "/tofu/v1/providers"


@dataclass
class Publisher:
    """A namespace that accepts uploads, signed by the fake registry's key."""

    bindings: Any
    monitor_task: Callable[[str], Any]
    namespace: Any
    repository: Any
    distribution: Any

    def publish(self, files: list[tuple[str, bytes]], token: str | None = None) -> Any:  # noqa: ANN401
        if token is None:
            response = self.bindings.PulpTofuNamespacesApi.publish(
                self.namespace.pulp_href, files=files
            )
        else:
            # A client with no basic credentials, so the bearer token is the only one.
            configuration = Configuration(host=PULP_URL)
            configuration.ssl_ca_cert = str(E2E / "certs" / "ca.crt")
            configuration.safe_chars_for_path_param = "/"
            api = PulpTofuNamespacesApi(ApiClient(configuration))
            wire: list[bytes | str | tuple[str, bytes]] = [*files]
            response = api.publish(
                self.namespace.pulp_href,
                files=wire,
                _headers={"Authorization": f"Bearer {token}"},
            )
        return self.monitor_task(response.task)


@pytest.fixture
def publisher(
    tofu_bindings: Any,
    pulpcore_bindings: Any,
    monitor_task: Callable[[str], Any],
    gen_object_with_cleanup: Callable[..., Any],
    tofu_repository_factory: Callable[..., Any],
    tofu_distribution_factory: Callable[..., Any],
    tofu_namespace_factory: Callable[..., Any],
    upstream: tuple[FakeRegistry, int, str],
) -> Publisher:
    registry, _, _ = upstream
    keyring = gen_object_with_cleanup(
        pulpcore_bindings.RepositoriesOpenpgpKeyringApi, {"name": secrets.token_hex(8)}
    )
    response = pulpcore_bindings.ContentOpenpgpPublickeyApi.create(
        file=("key.asc", registry.armored_key.encode()), repository=keyring.pulp_href
    )
    monitor_task(response.task)
    repository = tofu_repository_factory()
    distribution = tofu_distribution_factory(repository=repository, allow_uploads=True)
    namespace = tofu_namespace_factory(
        f"pub{secrets.token_hex(4)}", distribution, keyring=keyring.pulp_href
    )
    return Publisher(
        bindings=tofu_bindings,
        monitor_task=monitor_task,
        namespace=namespace,
        repository=repository,
        distribution=distribution,
    )


def _files(ptype: str, version: str, release: Release) -> list[tuple[str, bytes]]:
    prefix = f"terraform-provider-{ptype}_{version}"
    manifest = json.dumps({"version": 1, "metadata": {"protocol_versions": ["6.0"]}})
    return [
        *release.archives.items(),
        (f"{prefix}_SHA256SUMS", release.sums),
        (f"{prefix}_SHA256SUMS.sig", release.signature),
        (f"{prefix}_manifest.json", manifest.encode()),
    ]


def _release(registry: FakeRegistry, ptype: str) -> Release:
    release = registry.release(ptype, "1.0.0")
    assert release is not None
    return release


def test_published_release_is_served_by_the_registry(
    publisher: Publisher, upstream: tuple[FakeRegistry, int, str], provider_type: str
) -> None:
    registry, _, _ = upstream
    release = _release(registry, provider_type)
    publisher.publish(_files(provider_type, "1.0.0", release))

    base = f"{REGISTRY}/{publisher.namespace.name}/{provider_type}"
    versions = json.loads(fetch(f"{base}/versions").body)["versions"]
    assert [(v["version"], v["protocols"]) for v in versions] == [("1.0.0", ["6.0"])]
    document = json.loads(fetch(f"{base}/1.0.0/download/linux/amd64").body)
    archive = release.archives[document["filename"]]
    assert document["shasum"] == hashlib.sha256(archive).hexdigest()
    assert fetch(URL(document["download_url"]).path).body == archive
    hashes = document["packages"]["linux_amd64"]["hashes"]
    assert any(h.startswith("h1:") for h in hashes)

    # Publishing the same release again changes nothing.
    publisher.publish(_files(provider_type, "1.0.0", release))
    assert len(json.loads(fetch(f"{base}/versions").body)["versions"]) == 1


def test_a_published_version_cannot_change(
    publisher: Publisher, upstream: tuple[FakeRegistry, int, str], provider_type: str
) -> None:
    registry, _, _ = upstream
    release = _release(registry, provider_type)
    publisher.publish(_files(provider_type, "1.0.0", release))

    [name, *_] = release.archives
    changed = signed_release(
        registry.key, {**release.archives, name: release.archives[name] + b"\0"}
    )
    with pytest.raises(ApiException) as error:
        publisher.publish(_files(provider_type, "1.0.0", changed))
    assert error.value.status == 409


def test_release_signed_by_another_key_is_refused(
    publisher: Publisher,
    upstream: tuple[FakeRegistry, int, str],
    provider_type: str,
) -> None:
    registry, _, _ = upstream
    release = _release(registry, provider_type)
    files = _files(provider_type, "1.0.0", signed_release(registry.other_key, release.archives))
    with pytest.raises(PulpTaskError) as error:
        publisher.publish(files)
    assert "does not verify" in error.value.task.error["description"]
    base = f"{REGISTRY}/{publisher.namespace.name}/{provider_type}"
    assert fetch(f"{base}/versions").status == 404


def test_archives_must_match_the_signed_checksums(
    publisher: Publisher, upstream: tuple[FakeRegistry, int, str], provider_type: str
) -> None:
    registry, _, _ = upstream
    release = _release(registry, provider_type)
    [name, *_] = release.archives
    tampered = Release({**release.archives, name: b"tampered"}, release.sums, release.signature)
    with pytest.raises(PulpTaskError) as error:
        publisher.publish(_files(provider_type, "1.0.0", tampered))
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
    upstream: tuple[FakeRegistry, int, str],
    provider_type: str,
    tofu_bindings: Any,
    gen_object_with_cleanup: Callable[..., Any],
) -> None:
    registry, _, _ = upstream
    files = _files(provider_type, "1.0.0", _release(registry, provider_type))

    def token(scope: str) -> str:
        body = {"name": scope, "scope": scope, "namespaces": [publisher.namespace.pulp_href]}
        return str(gen_object_with_cleanup(tofu_bindings.PulpTofuTokensApi, body).token)

    with pytest.raises(ApiException) as error:
        publisher.publish(files, token=token("read"))
    assert error.value.status == 403
    publisher.publish(files, token=token("publish"))
    base = f"{REGISTRY}/{publisher.namespace.name}/{provider_type}"
    assert fetch(f"{base}/versions").status == 200
