"""Fixtures for functional tests against a running Pulp and a fake upstream registry.

The tests run inside the Pulp container, as Pulp's CI and oci-env run them: Pulp is
``API_PROTOCOL://API_HOST:API_PORT`` (pulpcore's ``bindings_cfg`` reads the same
variables), its certificate is in certifi's bundle, and ``pulpcore-manager`` is on
``PATH``. The fake registry runs in the test process; Pulp reaches it on
``127.0.0.1``.

Pulp objects are made through the generated bindings and pulpcore's fixtures; the
mirror endpoints are fetched with plain HTTP, as OpenTofu fetches them.
"""

import datetime
import http.client
import io
import ipaddress
import itertools
import json
import os
import secrets
import ssl
import subprocess
import tarfile
import urllib.error
import urllib.request
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import certifi
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from yarl import URL

from pulpcore.client.pulp_tofu import ApiClient, Configuration, PulpTofuNamespacesApi
from pulpcore.pytest_plugin import create_signing_service, remove_signing_service

from .fake_registry import NAMESPACE, FakeRegistry, Release, serve

PULP_URL = str(
    URL.build(
        scheme=os.environ.get("API_PROTOCOL", "https"),
        host=os.environ.get("API_HOST", "pulp"),
        port=int(os.environ.get("API_PORT", "443")),
    )
)
UPSTREAM_HOST = "127.0.0.1"
# Unique per session: content from earlier runs stays in Pulp and must not match.
PROVIDER_TYPES = [f"p{secrets.token_hex(4)}" for _ in range(40)]
VERSIONS = ["1.0.0", "1.1.0"]


def tls_context() -> ssl.SSLContext:
    """Verify against certifi's bundle, where Pulp's CI adds the webserver certificate."""
    return ssl.create_default_context(cafile=certifi.where())


@dataclass(frozen=True)
class Response:
    """A complete HTTP response, or the bytes read before the connection failed."""

    status: int
    body: bytes
    complete: bool


def fetch(path: str, headers: dict[str, str] | None = None) -> Response:
    """GET a Pulp path, keeping a partial body if the connection breaks."""
    request = urllib.request.Request(f"{PULP_URL}{path}", headers=headers or {})
    try:
        response = urllib.request.urlopen(request, context=tls_context(), timeout=120)
    except urllib.error.HTTPError as exc:
        return Response(status=exc.code, body=exc.read(), complete=True)
    with response:
        expected = response.headers.get("Content-Length")
        chunks: list[bytes] = []
        broken = False
        try:
            while chunk := response.read(64 * 1024):
                chunks.append(chunk)
        except http.client.IncompleteRead as exc:
            chunks.append(exc.partial)
            broken = True
        except OSError:
            broken = True
        body = b"".join(chunks)
        # A chunked body that stops early raises; a sized one just reads short.
        complete = not broken and (expected is None or len(body) == int(expected))
        return Response(status=response.status, body=body, complete=complete)


@dataclass
class Mirror:
    """One remote, repository and distribution serving one fake provider."""

    bindings: Any
    monitor_task: Callable[[str], Any]
    registry: FakeRegistry
    remote: Any
    repository: Any
    distribution: Any
    hostname: str
    provider_type: str

    def path(self, leaf: str) -> str:
        base = self.distribution.base_path
        return f"/pulp/tofu/{base}/{self.hostname}/{NAMESPACE}/{self.provider_type}/{leaf}"

    def get(self, leaf: str) -> Response:
        return fetch(self.path(leaf))

    def json(self, leaf: str) -> Any:  # noqa: ANN401
        response = self.get(leaf)
        assert response.status == 200, response
        return json.loads(response.body)

    def update_remote(self, **fields: object) -> None:
        response = self.bindings.RemotesTofuApi.partial_update(self.remote.pulp_href, fields)
        self.monitor_task(response.task)

    def packages(self) -> list[Any]:
        listing = self.bindings.ContentProviderPackagesApi.list(provider_type=self.provider_type)
        return list(listing.results)

    def present_content(self) -> dict[str, int]:
        repository = self.bindings.RepositoriesTofuApi.read(self.repository.pulp_href)
        version = self.bindings.RepositoriesTofuVersionsApi.read(repository.latest_version_href)
        return {key: value["count"] for key, value in version.content_summary.present.items()}


def tls_material(directory: Path, host: str) -> tuple[Path, Path, str]:
    """A throwaway CA and a certificate for ``host``, a hostname or an IP address.

    Returns the certificate and key paths and the CA in PEM.
    """
    try:
        san: x509.GeneralName = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        san = x509.DNSName(host)
    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-registry-ca")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = directory / "server.crt", directory / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path, ca.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(scope="session")
def upstream(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[tuple[FakeRegistry, int, str]]:
    cert, key, ca = tls_material(tmp_path_factory.mktemp("registry-tls"), UPSTREAM_HOST)
    registry = FakeRegistry(dict.fromkeys(PROVIDER_TYPES, VERSIONS))
    with serve(registry, cert=cert, key=key, host=UPSTREAM_HOST) as port:
        yield registry, port, ca


_provider_types = itertools.count()


@pytest.fixture
def provider_type() -> str:
    """A provider type the fake registry serves and no other test uses."""
    return PROVIDER_TYPES[next(_provider_types)]


@pytest.fixture
def mirror(
    tofu_bindings: Any,
    monitor_task: Callable[[str], Any],
    tofu_remote_factory: Callable[..., Any],
    tofu_repository_factory: Callable[..., Any],
    tofu_distribution_factory: Callable[..., Any],
    upstream: tuple[FakeRegistry, int, str],
) -> Generator[Mirror]:
    """A fresh pull-through mirror; each test gets a provider no other test has fetched."""
    registry, port, ca = upstream
    registry.state.reset()
    remote = tofu_remote_factory(url=f"https://{UPSTREAM_HOST}:{port}/", ca_cert=ca)
    repository = tofu_repository_factory()
    distribution = tofu_distribution_factory(remote=remote, repository=repository)
    yield Mirror(
        bindings=tofu_bindings,
        monitor_task=monitor_task,
        registry=registry,
        remote=remote,
        repository=repository,
        distribution=distribution,
        hostname=f"{UPSTREAM_HOST}:{port}",
        provider_type=PROVIDER_TYPES[next(_provider_types)],
    )
    registry.state.reset()


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
            configuration.ssl_ca_cert = certifi.where()
            configuration.safe_chars_for_path_param = "/"
            api = PulpTofuNamespacesApi(ApiClient(configuration))
            wire: list[bytes | str | tuple[str, bytes]] = [*files]
            response = api.publish(
                self.namespace.pulp_href,
                files=wire,
                _headers={"Authorization": f"Bearer {token}"},
            )
        return self.monitor_task(response.task)

    def publish_module(self, version: str, package: bytes, name: str = "vpc") -> Any:  # noqa: ANN401
        response = self.bindings.PulpTofuNamespacesApi.publish_module(
            self.namespace.pulp_href,
            name=name,
            system="aws",
            version=version,
            file=("vpc.tar.gz", package),
        )
        return self.monitor_task(response.task)


def module_package(*entries: tuple[str, bytes]) -> bytes:
    """A gzipped tarball holding ``(name, data)`` entries."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in entries:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def release_files(ptype: str, version: str, release: Release) -> list[tuple[str, bytes]]:
    """``release`` under GoReleaser's file names, as namespace publish takes it."""
    prefix = f"terraform-provider-{ptype}_{version}"
    manifest = json.dumps({"version": 1, "metadata": {"protocol_versions": ["6.0"]}})
    return [
        *release.archives.items(),
        (f"{prefix}_SHA256SUMS", release.sums),
        (f"{prefix}_SHA256SUMS.sig", release.signature),
        (f"{prefix}_manifest.json", manifest.encode()),
    ]


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


@pytest.fixture
def registry(upstream: tuple[FakeRegistry, int, str]) -> FakeRegistry:
    return upstream[0]


@pytest.fixture
def release(registry: FakeRegistry, provider_type: str) -> Release:
    """Version 1.0.0 of the test's provider, signed by the namespace's key."""
    found = registry.release(provider_type, "1.0.0")
    assert found is not None
    return found


_SIGNING_CLASS = "tofu:TofuSigningService"
_SIGNING_SCRIPT = """#!/bin/sh
set -eu
gpg --homedir '{home}' --batch --yes --local-user "$PULP_SIGNING_KEY_FINGERPRINT" \\
  --detach-sign --output "$1.sig" "$1"
printf '{{"file": "%s", "signature": "%s.sig"}}\\n' "$1" "$1"
"""


@pytest.fixture(scope="session")
def tofu_signing_service(
    tmp_path_factory: pytest.TempPathFactory, pulpcore_bindings: Any
) -> Generator[Any]:
    """A ``tofu:TofuSigningService`` with a new ed25519 key, binary detached signatures.

    Registered through ``pulpcore-manager``, so the tests must share Pulp's host.
    """
    home = tmp_path_factory.mktemp("tofu-gnupg")
    gpg = ("gpg", "--homedir", str(home), "--batch")
    subprocess.run(
        (*gpg, "--passphrase", "", "--quick-gen-key", "pulp_tofu test", "ed25519", "sign", "never"),
        check=True,
        capture_output=True,
    )
    listing = subprocess.run(
        (*gpg, "--list-keys", "--with-colons"), check=True, capture_output=True, text=True
    ).stdout
    fingerprint = next(
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr")
    )
    script = home / "sign.sh"
    script.write_text(_SIGNING_SCRIPT.format(home=home))
    script.chmod(0o755)
    name = create_signing_service(home, fingerprint, script, service_class=_SIGNING_CLASS)
    yield pulpcore_bindings.SigningServicesApi.list(name=name).results[0]
    remove_signing_service(name, service_class=_SIGNING_CLASS)
