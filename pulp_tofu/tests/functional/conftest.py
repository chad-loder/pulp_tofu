"""Fixtures for functional tests against a running Pulp and a fake upstream registry.

``dev/e2e/run.sh keep`` starts the Pulp these tests use: it listens on
``https://localhost:8443`` with the CA and admin netrc under ``.dev/e2e``. The fake
registry runs in the test process; Pulp reaches it as ``host.docker.internal``.

Pulp objects are made through the generated bindings and pulpcore's fixtures; the
mirror endpoints are fetched with plain HTTP, as OpenTofu fetches them.
"""

import datetime
import http.client
import itertools
import json
import netrc
import os
import secrets
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .fake_registry import NAMESPACE, FakeRegistry, serve

ROOT = Path(__file__).resolve().parents[3]
E2E = ROOT / ".dev" / "e2e"
PULP_URL = "https://localhost:8443"
UPSTREAM_HOST = "host.docker.internal"
# Unique per session: content from earlier runs stays in Pulp and must not match.
PROVIDER_TYPES = [f"p{secrets.token_hex(4)}" for _ in range(40)]
VERSIONS = ["1.0.0", "1.1.0"]

# pulpcore's pulp_settings fixture sets up Django with these settings.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pulp_dev_settings")


@dataclass(frozen=True)
class Response:
    """A complete HTTP response, or the bytes read before the connection failed."""

    status: int
    body: bytes
    complete: bool


def _fetch(path: str) -> Response:
    """GET a content path, keeping a partial body if the connection breaks."""
    context = ssl.create_default_context(cafile=str(E2E / "certs" / "ca.crt"))
    try:
        response = urllib.request.urlopen(f"{PULP_URL}{path}", context=context, timeout=120)
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
        return _fetch(self.path(leaf))

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


def _tls_material(directory: Path) -> tuple[Path, Path, str]:
    """A throwaway CA and a server certificate for the fake registry."""
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
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, UPSTREAM_HOST)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(UPSTREAM_HOST)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = directory / "registry.crt", directory / "registry.key"
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
def bindings_cfg() -> Any:  # noqa: ANN401
    """pulpcore's bindings configuration, pointed at the e2e Pulp and its CA."""
    from pulpcore.client.pulpcore import Configuration

    if not (E2E / "netrc").exists():
        pytest.skip("no test Pulp; run dev/e2e/run.sh keep")
    login = netrc.netrc(str(E2E / "netrc")).authenticators("pulp")
    if login is None:
        pytest.skip("the e2e netrc has no entry for pulp")
    configuration = Configuration(host=PULP_URL, username=login[0], password=login[2])
    configuration.ssl_ca_cert = str(E2E / "certs" / "ca.crt")
    configuration.safe_chars_for_path_param = "/"
    return configuration


@pytest.fixture(scope="session")
def upstream(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[tuple[FakeRegistry, int, str]]:
    cert, key, ca = _tls_material(tmp_path_factory.mktemp("registry-tls"))
    registry = FakeRegistry(dict.fromkeys(PROVIDER_TYPES, VERSIONS))
    with serve(registry, cert=cert, key=key) as port:
        yield registry, port, ca


_provider_types = itertools.count()


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
