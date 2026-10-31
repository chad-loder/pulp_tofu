"""``tofu init`` against Pulp, with the OpenTofu release binary.

These tests reach registry.opentofu.org, github.com and the OpenTofu release
downloads. OpenTofu requires a dotted registry hostname, so Pulp's registry is
``pulp.test``: a proxy in the test process answers ``CONNECT pulp.test:443`` with a
certificate for that name and relays to Pulp, and tunnels every other ``CONNECT``.
Pulp's ``TOFU_REGISTRY_HOSTNAME`` must be ``pulp.test``.
"""

import asyncio
import contextlib
import hashlib
import io
import os
import platform
import secrets
import ssl
import subprocess
import threading
import time
import urllib.request
import zipfile
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import certifi
import pytest
from yarl import URL

from .conftest import PULP_URL, Publisher, module_package, release_files, tls_context, tls_material
from .fake_registry import FakeRegistry, Release, signed_release

REGISTRY_HOST = "pulp.test"
TOFU_VERSION = "1.12.5"
# From tofu_1.12.5_SHA256SUMS.
TOFU_SHA256 = {
    "amd64": "dade9650e6b74fc7a8b986bd8717497d32f9e09cf82e479afef4977fa3085536",
    "arm64": "528f4eea63452bbddb30fa4f1780b57fac8d7676f9dda0f772e847bb62c1260a",
}
TOFU_ARCH = {"x86_64": "amd64", "aarch64": "arm64"}

RANDOM = """terraform {
  required_providers {
    random = {
      source  = "%s"
      version = "3.7.2"
    }
  }
}
"""


@pytest.fixture(autouse=True)
def _registry_hostname(pulp_settings: Any) -> None:  # noqa: ANN401
    if (
        not pulp_settings.TOFU_REGISTRY_ENABLED
        or pulp_settings.TOFU_REGISTRY_HOSTNAME != REGISTRY_HOST
    ):
        pytest.skip(f"needs TOFU_REGISTRY_ENABLED and TOFU_REGISTRY_HOSTNAME = {REGISTRY_HOST!r}")


@pytest.fixture(scope="session")
def tofu_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    arch = TOFU_ARCH.get(platform.machine())
    if arch is None:
        pytest.skip(f"no OpenTofu release for {platform.machine()}")
    name = f"tofu_{TOFU_VERSION}_linux_{arch}.zip"
    url = URL("https://github.com/opentofu/opentofu/releases/download") / f"v{TOFU_VERSION}" / name
    with urllib.request.urlopen(str(url), context=tls_context(), timeout=300) as response:
        data = response.read()
    assert hashlib.sha256(data).hexdigest() == TOFU_SHA256[arch], name
    directory = tmp_path_factory.mktemp("tofu-bin")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extract("tofu", directory)
    binary = directory / "tofu"
    binary.chmod(0o755)
    return binary


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError, ssl.SSLError):
        while data := await reader.read(64 * 1024):
            writer.write(data)
            await writer.drain()
    writer.close()


def _proxy_handler(
    front: ssl.SSLContext, pulp: URL
) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], Any]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        method, target, _ = head.split(b"\r\n", 1)[0].decode("ascii").split(" ", 2)
        address = URL(f"//{target}")
        if method != "CONNECT" or address.host is None or address.port is None:
            writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            writer.close()
            return
        if address.host == REGISTRY_HOST:
            assert pulp.host is not None
            upstream = await asyncio.open_connection(pulp.host, pulp.port, ssl=tls_context())
        else:
            upstream = await asyncio.open_connection(address.host, address.port)
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        if address.host == REGISTRY_HOST:
            await writer.start_tls(front)
        await asyncio.gather(_pipe(reader, upstream[1]), _pipe(upstream[0], writer))

    return handle


@pytest.fixture(scope="session")
def registry_proxy(tmp_path_factory: pytest.TempPathFactory) -> Generator[tuple[str, str]]:
    """The proxy URL, and the CA bundle that trusts both Pulp and the ``pulp.test`` front."""
    directory = tmp_path_factory.mktemp("registry-front")
    cert, key, ca = tls_material(directory, REGISTRY_HOST)
    front = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    front.load_cert_chain(cert, key)
    bundle = directory / "ca-bundle.pem"
    bundle.write_text(Path(certifi.where()).read_text() + ca)

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    start = asyncio.start_server(_proxy_handler(front, URL(PULP_URL)), "127.0.0.1", 0)
    server = asyncio.run_coroutine_threadsafe(start, loop).result(timeout=10)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", str(bundle)
    finally:
        loop.call_soon_threadsafe(server.close)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)


@dataclass
class Tofu:
    """Runs ``tofu init`` in a fresh directory with only the environment it needs."""

    binary: Path
    proxy: str
    ca_bundle: str
    root: Path

    def init(
        self, config: str, cli_config: str = "", token: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        work = Path(self.root, secrets.token_hex(4))
        work.mkdir()
        (work / "main.tf").write_text(config)
        (work / "tofurc").write_text(cli_config)
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(work),
            "HTTPS_PROXY": self.proxy,
            "SSL_CERT_FILE": self.ca_bundle,
            "TF_CLI_CONFIG_FILE": str(work / "tofurc"),
            "TF_IN_AUTOMATION": "1",
        }
        if token is not None:
            env["TF_TOKEN_pulp_test"] = token
        return subprocess.run(
            (str(self.binary), "init", "-input=false", "-no-color"),
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )


@pytest.fixture
def tofu(tofu_binary: Path, registry_proxy: tuple[str, str], tmp_path: Path) -> Tofu:
    proxy, bundle = registry_proxy
    return Tofu(binary=tofu_binary, proxy=proxy, ca_bundle=bundle, root=tmp_path)


def _passed(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _network_mirror(url: str) -> str:
    return f'provider_installation {{\n  network_mirror {{\n    url = "{url}"\n  }}\n}}\n'


def _wait_for_package(bindings: Any, repository: Any) -> None:  # noqa: ANN401
    for _ in range(60):
        latest = bindings.RepositoriesTofuApi.read(repository.pulp_href).latest_version_href
        version = bindings.RepositoriesTofuVersionsApi.read(latest)
        if "tofu.provider_package" in version.content_summary.present:
            return
        time.sleep(0.5)
    pytest.fail("the pulled-through package never reached the repository")


def test_init_pulls_through_then_installs_with_upstream_down(
    tofu: Tofu,
    tofu_bindings: Any,
    monitor_task: Callable[[str], Any],
    tofu_remote_factory: Callable[..., Any],
    tofu_repository_factory: Callable[..., Any],
    tofu_distribution_factory: Callable[..., Any],
) -> None:
    remote = tofu_remote_factory(url="https://registry.opentofu.org/", total_timeout=600)
    repository = tofu_repository_factory()
    distribution = tofu_distribution_factory(remote=remote, repository=repository)
    mirror = _network_mirror(distribution.base_url)

    first = _passed(tofu.init(RANDOM % "hashicorp/random", mirror))
    assert "Installed hashicorp/random v3.7.2 (verified checksum)" in first
    _wait_for_package(tofu_bindings, repository)

    response = tofu_bindings.RemotesTofuApi.partial_update(
        remote.pulp_href, {"url": "https://upstream.invalid/", "metadata_ttl": 0}
    )
    monitor_task(response.task)
    second = _passed(tofu.init(RANDOM % "hashicorp/random", mirror))
    assert "Installed hashicorp/random v3.7.2 (verified checksum)" in second


def test_init_from_pulp_as_the_registry_without_cli_config(
    tofu: Tofu,
    tofu_sync: Callable[..., Any],
    tofu_remote_factory: Callable[..., Any],
    tofu_repository_factory: Callable[..., Any],
    tofu_distribution_factory: Callable[..., Any],
    tofu_namespace_factory: Callable[..., Any],
) -> None:
    remote = tofu_remote_factory(url="https://registry.opentofu.org/", total_timeout=600)
    repository = tofu_repository_factory()
    distribution = tofu_distribution_factory(remote=remote, repository=repository)
    selection = {"namespace": "hashicorp", "type": "random", "versions": ["3.7.2"]}
    tofu_sync(repository, remote, [selection], platforms=[f"linux_{TOFU_ARCH[platform.machine()]}"])
    tofu_namespace_factory("hashicorp", distribution, content_hostname="registry.opentofu.org")

    output = _passed(tofu.init(RANDOM % f"{REGISTRY_HOST}/hashicorp/random"))
    assert f"Installed {REGISTRY_HOST}/hashicorp/random v3.7.2 (signed, key ID" in output


def _installable_release(registry: FakeRegistry, provider_type: str) -> Release:
    """Version 1.0.0 for both Linux platforms, each zip holding an executable provider."""
    archives: dict[str, bytes] = {}
    for arch in TOFU_SHA256:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            info = zipfile.ZipInfo(f"terraform-provider-{provider_type}_v1.0.0")
            info.external_attr = 0o755 << 16
            # OpenTofu 1.12 panics when two platforms of one version share a hash.
            archive.writestr(info, f"#!/bin/sh\n# linux_{arch}\nexit 1\n")
        archives[f"terraform-provider-{provider_type}_1.0.0_linux_{arch}.zip"] = buffer.getvalue()
    return signed_release(registry.key, archives)


def test_init_of_a_private_guarded_namespace_needs_a_token(
    tofu: Tofu,
    publisher: Publisher,
    registry: FakeRegistry,
    provider_type: str,
    pulpcore_bindings: Any,
    tofu_bindings: Any,
    gen_object_with_cleanup: Callable[..., Any],
) -> None:
    guard = gen_object_with_cleanup(
        pulpcore_bindings.ContentguardsContentRedirectApi, {"name": secrets.token_hex(8)}
    )
    # The bindings' patch model sends every field's default, so allow_uploads goes too.
    response = tofu_bindings.DistributionsTofuApi.partial_update(
        publisher.distribution.pulp_href, {"content_guard": guard.pulp_href, "allow_uploads": True}
    )
    publisher.monitor_task(response.task)
    tofu_bindings.PulpTofuNamespacesApi.partial_update(
        publisher.namespace.pulp_href, {"private": True}
    )
    release = _installable_release(registry, provider_type)
    publisher.publish(release_files(provider_type, "1.0.0", release))
    publisher.publish_module("1.0.0", module_package(("main.tf", b'variable "cidr" {}\n')))

    namespace = publisher.namespace.name
    config = (
        f"terraform {{\n  required_providers {{\n    demo = {{\n"
        f'      source  = "{REGISTRY_HOST}/{namespace}/{provider_type}"\n'
        f'      version = "1.0.0"\n    }}\n  }}\n}}\n\n'
        f'module "vpc" {{\n  source  = "{REGISTRY_HOST}/{namespace}/vpc/aws"\n'
        f'  version = "1.0.0"\n}}\n'
    )

    anonymous = tofu.init(config)
    assert anonymous.returncode != 0
    assert "401 Unauthorized" in anonymous.stderr

    token = gen_object_with_cleanup(
        tofu_bindings.PulpTofuTokensApi,
        {"name": "init", "namespaces": [publisher.namespace.pulp_href]},
    )
    output = _passed(tofu.init(config, token=str(token.token)))
    assert f"Downloading {REGISTRY_HOST}/{namespace}/vpc/aws 1.0.0 for vpc..." in output
    assert f"Installed {REGISTRY_HOST}/{namespace}/{provider_type} v1.0.0 (signed, key ID" in output


def test_init_of_a_module_synced_from_github(
    tofu: Tofu,
    tofu_bindings: Any,
    monitor_task: Callable[[str], Any],
    tofu_remote_factory: Callable[..., Any],
    tofu_repository_factory: Callable[..., Any],
    tofu_distribution_factory: Callable[..., Any],
    tofu_namespace_factory: Callable[..., Any],
) -> None:
    remote = tofu_remote_factory(url="https://registry.opentofu.org/")
    repository = tofu_repository_factory()
    distribution = tofu_distribution_factory(repository=repository)
    selection = {
        "namespace": "cloudposse",
        "name": "label",
        "system": "null",
        "versions": ["0.25.0"],
    }
    response = tofu_bindings.RepositoriesTofuApi.sync(
        repository.pulp_href, {"remote": remote.pulp_href, "modules": [selection]}
    )
    monitor_task(response.task)
    tofu_namespace_factory("cloudposse", distribution, content_hostname="registry.opentofu.org")

    config = f'module "label" {{\n  source  = "{REGISTRY_HOST}/cloudposse/label/null"\n  version = "0.25.0"\n}}\n'
    output = _passed(tofu.init(config))
    assert f"Downloading {REGISTRY_HOST}/cloudposse/label/null 0.25.0 for label..." in output
