"""A TLS provider registry with fault injection, for functional tests.

Serves the registry protocol for one namespace (``acme``) from a thread in the test
process. Archives are deterministic zips; ``SHA256SUMS`` is signed with a key made
per server. Faults are toggled on ``RegistryState`` between requests::

    registry.state.truncate_archive_after = 1024
"""

import hashlib
import io
import json
import random
import select
import socket
import ssl
import struct
import threading
import time
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, override

from pysequoia import SignatureMode, Tsk, sign

NAMESPACE = "acme"
PLATFORMS = (("linux", "amd64"), ("linux", "arm64"), ("darwin", "arm64"))
# Longer than any client timeout the tests expect to hit.
DROP_HOLD_SECONDS = 120.0
# Larger than pulpcore's 1 MiB download chunk, so archives stream in several writes.
ARCHIVE_BYTES = 3 * 1024 * 1024 // 2


@dataclass
class Release:
    """One provider version's files as the registry publishes them."""

    archives: dict[str, bytes]
    sums: bytes
    signature: bytes

    def filename(self, os_name: str, arch: str) -> str:
        (name,) = (n for n in self.archives if n.endswith(f"_{os_name}_{arch}.zip"))
        return name


@dataclass
class RegistryState:
    """Per-test faults and the request log."""

    truncate_archive_after: int | None = None
    truncate_once: bool = False
    archive_etag: bool = True
    corrupt_archive: bool = False
    archive_delay: float = 0.0
    down: bool = False
    bad_signature: bool = False
    unsigned: bool = False
    report_packages: bool = True
    # How the server reaps a keep-alive connection idle for ``idle_reap_after`` seconds:
    # "fin" closes it, "rst" resets it, "drop" keeps it open and never answers again,
    # as a NAT or firewall that forgot the connection would.
    idle_reap: Literal["fin", "rst", "drop"] | None = None
    idle_reap_after: float = 1.0
    # Bytes of padding added to the discovery document, to test the size limit.
    discovery_padding: int = 0
    # Every JSON document carries an ETag and answers a matching If-None-Match with 304.
    not_modified: int = 0
    if_none_match: list[str] = field(default_factory=list)
    connections: int = 0
    requests: list[str] = field(default_factory=list)

    def reset(self) -> None:
        """Clear every fault and the request log."""
        fresh = RegistryState()
        for name in fresh.__dataclass_fields__:
            setattr(self, name, getattr(fresh, name))

    def hits(self, fragment: str) -> int:
        """How many logged request paths contain ``fragment``."""
        return sum(fragment in path for path in self.requests)


class FakeRegistry:
    """Provider releases plus the server state that shapes their responses."""

    def __init__(self, providers: dict[str, list[str]]) -> None:
        self.key = Tsk.generate("registry@example.com")
        self.other_key = Tsk.generate("impostor@example.com")
        self.state = RegistryState()
        self.providers = providers
        self._releases: dict[tuple[str, str], Release] = {}
        self._lock = threading.Lock()

    def release(self, ptype: str, version: str) -> Release | None:
        """A provider version's files, built on first use."""
        if version not in self.providers.get(ptype, []):
            return None
        with self._lock:
            if (ptype, version) not in self._releases:
                self._releases[ptype, version] = self._release(ptype, version)
            return self._releases[ptype, version]

    @property
    def armored_key(self) -> str:
        """The public key the download documents offer."""
        return str(self.key.extract_certificate())

    def _release(self, ptype: str, version: str) -> Release:
        archives: dict[str, bytes] = {}
        for os_name, arch in PLATFORMS:
            name = f"terraform-provider-{ptype}_{version}_{os_name}_{arch}.zip"
            archives[name] = _archive(f"{ptype}-{version}-{os_name}-{arch}")
        sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in archives.items())
        signature = sign(self.key.signer(), sums.encode(), mode=SignatureMode.DETACHED, armor=False)
        return Release(archives=archives, sums=sums.encode(), signature=signature)

    def respond(self, path: str) -> tuple[int, str, bytes, "_BodyFault"]:
        """Map a request path to status, content type, body and body fault."""
        self.state.requests.append(path)
        if self.state.down:
            return 503, "text/plain", b"down\n", _NO_FAULT
        parts = path.strip("/").split("/")
        if path == "/.well-known/terraform.json":
            document: dict[str, object] = {"providers.v1": "/v1/providers/"}
            if self.state.discovery_padding:
                document["padding"] = "x" * self.state.discovery_padding
            return _json(document)
        if parts[:3] == ["v1", "providers", NAMESPACE] and len(parts) == 5:
            return self._versions(parts[3])
        if parts[:3] == ["v1", "providers", NAMESPACE] and len(parts) == 8:
            return self._download(parts[3], parts[4], parts[6], parts[7])
        if parts[0] == "files" and len(parts) == 4:
            return self._file(parts[1], parts[2], parts[3])
        return 404, "text/plain", b"not found\n", _NO_FAULT

    def _versions(self, ptype: str) -> tuple[int, str, bytes, "_BodyFault"]:
        if ptype not in self.providers:
            return 404, "text/plain", b"not found\n", _NO_FAULT
        platforms = [{"os": o, "arch": a} for o, a in PLATFORMS]
        versions = [
            {"version": v, "protocols": ["5.0"], "platforms": platforms}
            for v in self.providers[ptype]
        ]
        return _json({"versions": versions})

    def _download(
        self, ptype: str, version: str, os_name: str, arch: str
    ) -> tuple[int, str, bytes, "_BodyFault"]:
        release = self.release(ptype, version)
        if release is None:
            return 404, "text/plain", b"not found\n", _NO_FAULT
        filename = release.filename(os_name, arch)
        keys = [] if self.state.unsigned else [{"key_id": "test", "ascii_armor": self.armored_key}]
        document: dict[str, object] = {
            "protocols": ["5.0"],
            "os": os_name,
            "arch": arch,
            "filename": filename,
            "download_url": f"/files/{ptype}/{version}/{filename}",
            "shasums_url": f"/files/{ptype}/{version}/SHA256SUMS",
            "shasums_signature_url": f"/files/{ptype}/{version}/SHA256SUMS.sig",
            "shasum": hashlib.sha256(release.archives[filename]).hexdigest(),
            "signing_keys": {"gpg_public_keys": keys},
        }
        if self.state.report_packages:
            document["packages"] = {
                f"{o}_{a}": {
                    "hashes": [
                        "zh:" + hashlib.sha256(release.archives[release.filename(o, a)]).hexdigest()
                    ],
                    "package_size": len(release.archives[release.filename(o, a)]),
                }
                for o, a in PLATFORMS
            }
        return _json(document)

    def _file(self, ptype: str, version: str, name: str) -> tuple[int, str, bytes, "_BodyFault"]:
        release = self.release(ptype, version)
        if release is None:
            return 404, "text/plain", b"not found\n", _NO_FAULT
        if name == "SHA256SUMS":
            return 200, "text/plain", release.sums, _NO_FAULT
        if name == "SHA256SUMS.sig":
            signature = release.signature
            if self.state.bad_signature:
                signature = sign(
                    self.other_key.signer(), release.sums, mode=SignatureMode.DETACHED, armor=False
                )
            return 200, "application/octet-stream", signature, _NO_FAULT
        data = release.archives.get(name)
        if data is None:
            return 404, "text/plain", b"not found\n", _NO_FAULT
        if self.state.corrupt_archive:
            data = data[:-1] + bytes([data[-1] ^ 0xFF])
        etag = f'"{hashlib.sha256(data).hexdigest()[:16]}"' if self.state.archive_etag else None
        fault = _BodyFault(
            truncate_after=self.state.truncate_archive_after,
            delay=self.state.archive_delay,
            etag=etag,
        )
        if self.state.truncate_once:
            self.state.truncate_archive_after = None
        return 200, "application/zip", data, fault


@dataclass(frozen=True)
class _BodyFault:
    truncate_after: int | None = None
    delay: float = 0.0
    etag: str | None = None


_NO_FAULT = _BodyFault()


def _json(document: object) -> tuple[int, str, bytes, _BodyFault]:
    return 200, "application/json", json.dumps(document).encode(), _NO_FAULT


def _archive(seed: str) -> bytes:
    rng = random.Random(seed)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(f"terraform-provider-{seed}", rng.randbytes(ARCHIVE_BYTES))
    return buffer.getvalue()


def _range_start(range_header: str | None, if_range: str | None, fault: _BodyFault) -> int | None:
    """The first byte of an open-ended ``bytes=N-`` range the server should honor."""
    if fault.etag is None or range_header is None or not range_header.startswith("bytes="):
        return None
    if if_range is not None and if_range != fault.etag:
        return None
    first, _, last = range_header.removeprefix("bytes=").partition("-")
    return int(first) if first.isdigit() and not last else None


def _handler(registry: FakeRegistry) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        @override
        def handle(self) -> None:
            registry.state.connections += 1
            self.close_connection = True
            self.handle_one_request()
            while not self.close_connection:
                if not self._wait_for_next_request():
                    return
                self.handle_one_request()

        def _wait_for_next_request(self) -> bool:
            """Wait for the next keep-alive request; False once the connection is reaped."""
            mode = registry.state.idle_reap
            if mode is None:
                return True
            pending = isinstance(self.connection, ssl.SSLSocket) and self.connection.pending()
            ready, _, _ = select.select([self.connection], [], [], registry.state.idle_reap_after)
            if pending or ready:
                return True
            if mode == "rst":
                self.connection.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
            elif mode == "drop":
                time.sleep(DROP_HOLD_SECONDS)
            self.close_connection = True
            return False

        def do_GET(self) -> None:
            status, content_type, body, fault = registry.respond(self.path)
            if status == 200 and content_type == "application/json":
                etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
                if (validator := self.headers.get("If-None-Match")) is not None:
                    registry.state.if_none_match.append(validator)
                if validator == etag:
                    registry.state.not_modified += 1
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                fault = _BodyFault(etag=etag)
            start = _range_start(self.headers.get("Range"), self.headers.get("If-Range"), fault)
            total = len(body)
            if status == 200 and start is not None and start < total:
                status, body = 206, body[start:]
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if fault.etag is not None:
                self.send_header("ETag", fault.etag)
                self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{total - 1}/{total}")
            self.end_headers()
            if fault.delay:
                time.sleep(fault.delay)
            if fault.truncate_after is not None:
                self.wfile.write(body[: fault.truncate_after])
                self.wfile.flush()
                self.close_connection = True
                self.connection.close()
                return
            self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


@contextmanager
def serve(
    registry: FakeRegistry, *, cert: Path, key: Path, host: str = "0.0.0.0"
) -> Generator[int]:
    """Serve ``registry`` over TLS in a thread; yield the bound port."""
    server = _Server((host, 0), _handler(registry))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
