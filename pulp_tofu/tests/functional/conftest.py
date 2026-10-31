"""Fixtures for functional tests against a running Pulp and a fake upstream registry.

``dev/e2e/run.sh keep`` starts the Pulp these tests use: it listens on
``https://localhost:8443`` with the CA and admin netrc under ``.dev/e2e``. The fake
registry runs in the test process; Pulp reaches it as ``host.docker.internal``.
"""

import base64
import datetime
import http.client
import itertools
import json
import netrc
import secrets
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .fake_registry import NAMESPACE, FakeRegistry, serve

E2E = Path(__file__).resolve().parents[3] / ".dev" / "e2e"
PULP_URL = "https://localhost:8443"
UPSTREAM_HOST = "host.docker.internal"
# Unique per session: content from earlier runs stays in Pulp and must not match.
PROVIDER_TYPES = [f"p{secrets.token_hex(4)}" for _ in range(40)]
VERSIONS = ["1.0.0", "1.1.0"]


@dataclass(frozen=True)
class Response:
    """A complete HTTP response, or the bytes read before the connection failed."""

    status: int
    body: bytes
    complete: bool


class Pulp:
    """A minimal REST and content client for the test Pulp."""

    def __init__(self) -> None:
        self.context = ssl.create_default_context(cafile=str(E2E / "certs" / "ca.crt"))
        login = netrc.netrc(str(E2E / "netrc")).authenticators("pulp")
        assert login is not None
        token = base64.b64encode(f"{login[0]}:{login[2]}".encode()).decode()
        self._auth = f"Basic {token}"

    def api(self, method: str, path: str, body: object | None = None) -> Any:  # noqa: ANN401
        url = path if path.startswith("https://") else f"{PULP_URL}{path}"
        request = urllib.request.Request(url, method=method)
        request.add_header("Authorization", self._auth)
        data = None
        if body is not None:
            request.add_header("Content-Type", "application/json")
            data = json.dumps(body).encode()
        with urllib.request.urlopen(request, data=data, context=self.context) as response:
            raw = response.read()
        result = json.loads(raw) if raw else {}
        return result

    def wait(self, result: dict[str, object]) -> None:
        """Wait for the task in an asynchronous API response."""
        task = result.get("task")
        if task is None:
            return
        for _ in range(120):
            state = self.api("GET", str(task))["state"]
            if state == "completed":
                return
            assert state not in ("failed", "canceled"), self.api("GET", str(task))
            time.sleep(0.5)
        raise AssertionError(f"task {task} did not finish")

    def fetch(self, path: str) -> Response:
        """GET a content path, keeping a partial body if the connection breaks."""
        request = urllib.request.Request(f"{PULP_URL}{path}")
        try:
            response = urllib.request.urlopen(request, context=self.context, timeout=120)
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

    pulp: Pulp
    registry: FakeRegistry
    remote: str
    repository: str
    base_path: str
    hostname: str
    provider_type: str

    def path(self, leaf: str) -> str:
        return (
            f"/pulp/tofu/{self.base_path}/{self.hostname}/{NAMESPACE}/{self.provider_type}/{leaf}"
        )

    def get(self, leaf: str) -> Response:
        return self.pulp.fetch(self.path(leaf))

    def json(self, leaf: str) -> Any:  # noqa: ANN401
        response = self.get(leaf)
        assert response.status == 200, response
        return json.loads(response.body)

    def update_remote(self, **fields: object) -> None:
        self.pulp.wait(self.pulp.api("PATCH", self.remote, fields))

    def packages(self) -> list[dict[str, Any]]:
        query = f"/pulp/api/v3/content/tofu/provider_packages/?provider_type={self.provider_type}"
        results: list[dict[str, Any]] = self.pulp.api("GET", query)["results"]
        return results


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
def pulp() -> Pulp:
    if not (E2E / "netrc").exists():
        pytest.skip("no test Pulp; run dev/e2e/run.sh keep")
    client = Pulp()
    try:
        client.api("GET", "/pulp/api/v3/status/")
    except OSError:
        pytest.skip("test Pulp is not reachable on https://localhost:8443")
    return client


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
def mirror(pulp: Pulp, upstream: tuple[FakeRegistry, int, str]) -> Generator[Mirror]:
    """A fresh pull-through mirror; each test gets a provider no other test has fetched."""
    registry, port, ca = upstream
    registry.state.reset()
    provider_type = PROVIDER_TYPES[next(_provider_types)]
    suffix = f"{provider_type}-{time.time_ns()}"
    remote = pulp.api(
        "POST",
        "/pulp/api/v3/remotes/tofu/tofu/",
        {
            "name": f"fake-{suffix}",
            "url": f"https://{UPSTREAM_HOST}:{port}/",
            "ca_cert": ca,
            "total_timeout": 60,
        },
    )
    repository = pulp.api("POST", "/pulp/api/v3/repositories/tofu/tofu/", {"name": suffix})
    created = pulp.api(
        "POST",
        "/pulp/api/v3/distributions/tofu/tofu/",
        {
            "name": suffix,
            "base_path": suffix,
            "remote": remote["pulp_href"],
            "repository": repository["pulp_href"],
        },
    )
    pulp.wait(created)
    yield Mirror(
        pulp=pulp,
        registry=registry,
        remote=str(remote["pulp_href"]),
        repository=str(repository["pulp_href"]),
        base_path=suffix,
        hostname=f"{UPSTREAM_HOST}:{port}",
        provider_type=provider_type,
    )
    registry.state.reset()
    distributions = pulp.api("GET", f"/pulp/api/v3/distributions/tofu/tofu/?name={suffix}")
    for distribution in distributions["results"]:
        pulp.wait(pulp.api("DELETE", distribution["pulp_href"]))
    pulp.wait(pulp.api("DELETE", str(repository["pulp_href"])))
    pulp.wait(pulp.api("DELETE", str(remote["pulp_href"])))
