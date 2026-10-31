"""Client for the upstream OpenTofu provider registry protocol.

Implements the calls a mirror needs from
https://opentofu.org/docs/internals/provider-registry-protocol/: service discovery,
``versions`` and ``download``. Each response is parsed once, here, into frozen value
objects; nothing past this module touches upstream JSON.
"""

import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from string import hexdigits
from typing import TYPE_CHECKING, cast

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout
from asgiref.sync import sync_to_async
from yarl import URL

from pulpcore.plugin.exceptions import TimeoutException

from .protocol import Platform, ProviderAddress

if TYPE_CHECKING:
    from pulpcore.plugin.download import DownloaderFactory

    from .models import TofuRemote

DISCOVERY_PATH = ".well-known/terraform.json"
PROVIDERS_SERVICE = "providers.v1"

# Upper bound on any registry JSON document or SHA256SUMS file read into memory.
MAX_METADATA_BYTES = 16 * 1024 * 1024

# Seconds for the first, pooled attempt at a metadata request when the remote sets no
# timeout of its own. Registry documents are small, so a read that stalls this long is a
# dead connection, not a slow one.
PROBE_READ_TIMEOUT = 5.0
PROBE_CONNECT_TIMEOUT = 10.0
PROBE_TOTAL_TIMEOUT = 60.0


class UpstreamError(Exception):
    """The upstream registry failed or answered outside its protocol."""


class UpstreamNotFound(UpstreamError):
    """The upstream registry answered 404."""


class UpstreamUnreachable(UpstreamError):
    """The connection to the upstream registry failed or timed out."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UpstreamVersion:
    """One entry of a ``versions`` response."""

    version: str
    protocols: tuple[str, ...]
    platforms: tuple[Platform, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportedPackage:
    """One entry of a ``download`` response's ``packages`` map."""

    zh: str | None
    size: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DownloadInfo:
    """A ``download`` response, with every URL resolved to absolute form."""

    platform: Platform
    protocols: tuple[str, ...]
    filename: str
    download_url: str
    shasums_url: str
    shasums_signature_url: str
    shasum: str
    signing_keys: tuple[str, ...]
    packages: Mapping[str, ReportedPackage]


def parse_discovery(document: object, base_url: URL) -> URL:
    """Return the absolute ``providers.v1`` base URL from a discovery document."""
    services = _object(document, "discovery document")
    value = services.get(PROVIDERS_SERVICE)
    if not isinstance(value, str) or not value:
        raise UpstreamError("the registry does not offer providers.v1")
    return URL(_https_url(base_url, value))


def parse_versions(document: object) -> list[UpstreamVersion]:
    """Parse a ``versions`` response, skipping entries with an invalid version string."""
    versions = _object(document, "versions response").get("versions")
    if not isinstance(versions, list):
        raise UpstreamError("versions response has no versions list")
    parsed: list[UpstreamVersion] = []
    for raw in cast("list[object]", versions):
        entry = _object(raw, "versions entry")
        version = entry.get("version")
        if not isinstance(version, str) or not _is_segment(version):
            continue
        platforms: list[Platform] = []
        for raw_platform in _list(entry.get("platforms", []), "platforms"):
            platform = _platform(_object(raw_platform, "platform"))
            if platform is not None:
                platforms.append(platform)
        parsed.append(
            UpstreamVersion(
                version=version,
                protocols=_strings(entry.get("protocols", []), "protocols"),
                platforms=tuple(platforms),
            )
        )
    return parsed


def parse_download(document: object, base_url: URL) -> DownloadInfo:
    """Parse a ``download`` response; relative URLs resolve against ``base_url``."""
    doc = _object(document, "download response")
    platform = _platform(doc)
    if platform is None:
        raise UpstreamError("download response has no valid os and arch")
    filename = _string(doc, "filename")
    if not _is_segment(filename):
        raise UpstreamError("download response filename is not a single path segment")
    shasum = _string(doc, "shasum").lower()
    if not _is_sha256(shasum):
        raise UpstreamError("download response shasum is not a SHA-256 hex digest")
    keys: list[str] = []
    signing_keys = doc.get("signing_keys", {})
    for raw_key in _list(_object(signing_keys, "signing_keys").get("gpg_public_keys", []), "keys"):
        armor = _object(raw_key, "gpg public key").get("ascii_armor")
        if isinstance(armor, str) and armor.strip():
            keys.append(armor)
    return DownloadInfo(
        platform=platform,
        protocols=_strings(doc.get("protocols", []), "protocols"),
        filename=filename,
        download_url=_https_url(base_url, _string(doc, "download_url")),
        shasums_url=_https_url(base_url, _string(doc, "shasums_url")),
        shasums_signature_url=_https_url(base_url, _string(doc, "shasums_signature_url")),
        shasum=shasum,
        signing_keys=tuple(keys),
        packages=_packages(doc.get("packages")),
    )


@dataclass(slots=True, kw_only=True)
class _Session:
    """One remote configuration's long-lived downloader factory and discovered service."""

    stamp: object
    loop: asyncio.AbstractEventLoop
    factory: "DownloaderFactory"
    http: ClientSession
    created_at: float = field(default_factory=time.monotonic)
    leases: int = 0
    retired: bool = False
    providers_url: URL | None = None


class SessionPool:
    """Long-lived HTTP sessions for registry metadata, one per remote per event loop.

    Callers hold a session only inside ``async with pool.lease(remote)``. The pool owns
    each session's lifecycle: it retires a session when its remote is edited, when it
    reaches ``max_age``, or on the first transport failure, since that failure usually
    means the upstream reaped idle connections the session still holds. A retired
    session closes when its last lease returns. ``close_all`` runs at content-app
    shutdown. Sessions are per event loop because aiohttp binds them to one; task
    workers run each task's pipeline on a loop of its own.
    """

    def __init__(self, *, max_age: float = 900.0) -> None:
        self._max_age = max_age
        self._current: dict[tuple[int, object], _Session] = {}
        self._sessions: dict[int, _Session] = {}
        self._closing: set[asyncio.Task[None]] = set()

    @asynccontextmanager
    async def lease(self, remote: "TofuRemote") -> AsyncGenerator[_Session]:
        """Borrow the remote's session for the body of the ``with`` block."""
        session = self._checkout(remote)
        try:
            yield session
        except UpstreamUnreachable:
            self._retire(session)
            raise
        finally:
            # Synchronous, so a cancelled request cannot skip it.
            self._checkin(session)

    async def close_all(self) -> None:
        """Close every session; the pool is empty afterwards."""
        sessions = list(self._sessions.values())
        self._current.clear()
        self._sessions.clear()
        for session in sessions:
            await session.http.close()

    def _checkout(self, remote: "TofuRemote") -> _Session:
        loop = asyncio.get_running_loop()
        key = (id(loop), remote.pk)
        session = self._current.get(key)
        if session is not None and (session.loop is not loop or self._worn_out(session, remote)):
            self._retire(session)
            session = None
        if session is None:
            session = self._open(remote, loop)
            self._current[key] = session
        session.leases += 1
        return session

    def _checkin(self, session: _Session) -> None:
        session.leases -= 1
        if session.retired and session.leases == 0:
            self._close(session)

    def _open(self, remote: "TofuRemote", loop: asyncio.AbstractEventLoop) -> _Session:
        # pulpcore.plugin.download reads Django settings on import; the parsers above
        # stay importable without them.
        from pulpcore.plugin.download import DownloaderFactory

        factory = DownloaderFactory(remote)
        # Every downloader a factory builds shares the factory's session.
        http: ClientSession = getattr(factory.build(remote.url), "session")  # noqa: B009
        session = _Session(stamp=remote.pulp_last_updated, loop=loop, factory=factory, http=http)
        self._sessions[id(session)] = session
        return session

    def _worn_out(self, session: _Session, remote: "TofuRemote") -> bool:
        return (
            session.retired
            or session.stamp != remote.pulp_last_updated
            or time.monotonic() - session.created_at > self._max_age
            or session.http.closed
        )

    def _retire(self, session: _Session) -> None:
        session.retired = True
        for key, current in list(self._current.items()):
            if current is session:
                del self._current[key]
        if session.leases == 0:
            self._close(session)

    def _close(self, session: _Session) -> None:
        self._sessions.pop(id(session), None)
        if session.loop.is_closed():
            return  # Its connections went with the loop.
        # Nothing waits on the close; the set keeps the task alive until it finishes.
        task = session.loop.create_task(session.http.close())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)


POOL = SessionPool()


class RegistryClient:
    """Async calls to one upstream registry through a remote's downloaders.

    Downloads honor the remote's TLS, proxy, timeout, retry and header settings. Each
    call leases the remote's pooled session, so metadata requests reuse kept-alive
    connections across client requests.
    """

    def __init__(self, remote: "TofuRemote") -> None:
        self._remote = remote

    async def providers_url(self) -> URL:
        """Resolve the registry's ``providers.v1`` base URL by service discovery, once."""
        async with POOL.lease(self._remote) as session:
            known = session.providers_url
        if known is not None:
            return known
        discovery = URL(self._remote.url).join(URL(DISCOVERY_PATH))
        document = await self.fetch_json(discovery)
        async with POOL.lease(self._remote) as session:
            session.providers_url = parse_discovery(document, discovery)
            return session.providers_url

    async def versions(self, address: ProviderAddress) -> list[UpstreamVersion]:
        """List a provider's versions."""
        url = await self._provider_url(address.namespace, address.type, "versions")
        return parse_versions(await self.fetch_json(url))

    async def download(
        self, address: ProviderAddress, version: str, platform: Platform
    ) -> DownloadInfo:
        """Fetch the ``download`` document for one platform of a provider version."""
        url = await self._provider_url(
            address.namespace, address.type, version, "download", platform.os, platform.arch
        )
        return parse_download(await self.fetch_json(url), url)

    async def _provider_url(self, *segments: str) -> URL:
        # joinpath resolves "." and ".." and splits on "/", so each value must be one segment.
        if not all(_is_segment(segment) for segment in segments):
            raise UpstreamNotFound("/".join(segments))
        return (await self.providers_url()).joinpath(*segments)

    async def fetch_json(self, url: URL) -> object:
        """Fetch and decode a JSON document."""
        body = await self.fetch_bytes(url)
        try:
            return cast("object", json.loads(body))
        except ValueError as exc:
            raise UpstreamError(f"{url} did not return JSON") from exc

    async def fetch_bytes(self, url: URL | str) -> bytes:
        """Fetch a small document into memory.

        The first attempt runs on the pooled session without pulpcore's retries and with
        a short read timeout, so a connection the upstream reaped while idle fails fast:
        at once after a close or reset, within ``PROBE_READ_TIMEOUT`` after a silent
        drop. That failure retires the session, and the second attempt runs on fresh
        connections with the remote's normal retries.

        Raises:
            UpstreamNotFound: The server answered 404.
            UpstreamError: Any other failure, or a body over ``MAX_METADATA_BYTES``.
        """
        try:
            path = await self._fetch_to_file(url, probe=True)
        except UpstreamUnreachable:
            path = await self._fetch_to_file(url, probe=False)
        try:
            return await sync_to_async(_read_bounded, thread_sensitive=False)(path)
        finally:
            await sync_to_async(_unlink, thread_sensitive=False)(path)

    async def _fetch_to_file(self, url: URL | str, *, probe: bool) -> Path:
        remote = self._remote
        options: dict[str, object] = {}
        extra_data: dict[str, object] = {}
        if probe:
            options["max_retries"] = 0
            timeout = ClientTimeout(
                total=remote.total_timeout or PROBE_TOTAL_TIMEOUT,
                sock_connect=remote.sock_connect_timeout or PROBE_CONNECT_TIMEOUT,
                sock_read=remote.sock_read_timeout or PROBE_READ_TIMEOUT,
            )
            extra_data["request_kwargs"] = {"timeout": timeout}
        async with POOL.lease(remote) as session:
            downloader = remote.get_downloader(
                url=str(url), download_factory=session.factory, **options
            )
            try:
                result = await downloader.run(extra_data=extra_data)
            except ClientResponseError as exc:
                if exc.status == 404:
                    raise UpstreamNotFound(url) from exc
                raise UpstreamError(f"{url} answered {exc.status}") from exc
            except (ClientError, OSError, TimeoutError, TimeoutException) as exc:
                raise UpstreamUnreachable(f"{url} failed: {exc}") from exc
        return Path(result.path)


def _read_bounded(path: Path) -> bytes:
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise UpstreamError("upstream metadata exceeds the size limit")
    return path.read_bytes()


def _unlink(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and set(value) <= set(hexdigits.lower())


def _is_segment(value: str) -> bool:
    """Return whether ``value`` stays one path segment when joined into a URL."""
    return bool(value) and "/" not in value and value not in (".", "..")


def _object(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise UpstreamError(f"{what} is not a JSON object")
    return cast("dict[str, object]", value)


def _list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise UpstreamError(f"{what} is not a JSON list")
    return cast("list[object]", value)


def _string(doc: dict[str, object], key: str) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value:
        raise UpstreamError(f"{key} is missing or not a string")
    return value


def _strings(value: object, what: str) -> tuple[str, ...]:
    return tuple(item for item in _list(value, what) if isinstance(item, str))


def _platform(doc: dict[str, object]) -> Platform | None:
    os_name, arch = doc.get("os"), doc.get("arch")
    if isinstance(os_name, str) and isinstance(arch, str) and os_name and arch:
        return Platform(os=os_name, arch=arch)
    return None


def _https_url(base_url: URL, value: str) -> str:
    resolved = base_url.join(URL(value))
    if resolved.scheme != "https" or not resolved.host:
        raise UpstreamError("registry URLs must be https")
    return str(resolved)


def _packages(value: object) -> dict[str, ReportedPackage]:
    if value is None:
        return {}
    packages: dict[str, ReportedPackage] = {}
    for key, raw in _object(value, "packages").items():
        entry = _object(raw, "packages entry")
        zh = None
        for item in _strings(entry.get("hashes", []), "hashes"):
            digest = item.removeprefix("zh:")
            if digest != item and _is_sha256(digest):
                zh = digest
        size = entry.get("package_size")
        packages[key] = ReportedPackage(
            zh=zh, size=size if isinstance(size, int) and size > 0 else None
        )
    return packages
