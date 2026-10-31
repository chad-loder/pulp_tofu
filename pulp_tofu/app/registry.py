"""Client for the upstream OpenTofu provider registry protocol.

Implements the calls a mirror needs from
https://opentofu.org/docs/internals/provider-registry-protocol/: service discovery,
``versions`` and ``download``. Each response is parsed once, here, into frozen value
objects; nothing past this module touches upstream JSON.
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, TypeGuard, cast

from aiohttp import (
    BasicAuth,
    ClientConnectionError,
    ClientPayloadError,
    ClientResponseError,
    ClientSession,
    ClientTimeout,
)
from pyhaul.transport import TransportHeaders
from pyhaul.transport.aiohttp_adapter import headers_from_aiohttp_response
from yarl import URL

from .flight import SingleFlight
from .hashes import ZH_PREFIX, Sha256Hex, parse_sha256
from .http_cache import (
    NEGATIVE_TTL,
    CacheEntry,
    HttpCache,
    freshness,
    kept_headers,
    monotonic,
)
from .protocol import Platform, PlatformKey, ProviderAddress, parse_platform_key
from .retry import is_retryable, retry_delay
from .signing import ARCHIVE_NAME

if TYPE_CHECKING:
    from pulpcore.plugin.download import HttpDownloader

    from .models import TofuRemote


class _Throttler(Protocol):
    """The part of ``asyncio_throttle.Throttler`` that pulpcore's remotes provide."""

    async def acquire(self) -> None: ...


DISCOVERY_PATH = ".well-known/terraform.json"
PROVIDERS_SERVICE = "providers.v1"
MODULES_SERVICE = "modules.v1"

MAX_METADATA_BYTES = 16 * 1024 * 1024
# A discovery document lists a few service URLs.
MAX_DISCOVERY_BYTES = 64 * 1024

# Timeouts for metadata requests when the remote sets none. A healthy but busy registry
# answers well inside the read timeout; a connection the network silently dropped
# fails it and is replaced.
METADATA_READ_TIMEOUT = 30.0
METADATA_CONNECT_TIMEOUT = 10.0
METADATA_TOTAL_TIMEOUT = 120.0

# pulpcore's Remote.DEFAULT_MAX_RETRIES, without importing models here.
DEFAULT_MAX_RETRIES = 3

# Upstream version strings become path segments and stored fields: semver characters only.
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.+-]+)?$")

_TRANSPORT_ERRORS = (ClientConnectionError, ClientPayloadError, OSError, TimeoutError)


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

    zh: Sha256Hex | None
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
    shasum: Sha256Hex
    signing_keys: tuple[str, ...]
    packages: Mapping[PlatformKey, ReportedPackage]


def parse_discovery(document: object, base_url: URL, service: str = PROVIDERS_SERVICE) -> URL:
    """Return the absolute base URL of ``service`` from a discovery document."""
    services = _object(document, "discovery document")
    value = services.get(service)
    if not isinstance(value, str) or not value:
        raise UpstreamError(f"the registry does not offer {service}")
    return URL(_https_url(base_url, value))


def parse_module_versions(document: object) -> list[str]:
    """The version strings of a module ``versions`` response's first module."""
    modules = _list(_object(document, "module versions response").get("modules"), "modules")
    if not modules:
        return []
    entries = _list(_object(modules[0], "modules entry").get("versions"), "versions")
    return [
        version
        for raw in entries
        if isinstance(version := _object(raw, "versions entry").get("version"), str)
    ]


def parse_module_location(status: int, headers: Mapping[str, str], body: bytes) -> str:
    """A module ``download`` answer's location: JSON ``location``, else ``X-Terraform-Get``.

    OpenTofu reads the body first and falls back to the header when the body is empty
    or names no location.
    """
    if status == 200 and body.strip():
        try:
            document: object = json.loads(body)
        except ValueError as exc:
            raise UpstreamError("module download response is not JSON") from exc
        location = _object(document, "module download response").get("location")
        if isinstance(location, str) and location:
            return location
    header = headers.get("X-Terraform-Get")
    if not header:
        raise UpstreamError("module download response names no location")
    return header


def parse_versions(document: object) -> list[UpstreamVersion]:
    """Parse a ``versions`` response, skipping entries with an invalid version string."""
    versions = _object(document, "versions response").get("versions")
    parsed: list[UpstreamVersion] = []
    for raw in _list(versions, "versions"):
        entry = _object(raw, "versions entry")
        version = entry.get("version")
        if not isinstance(version, str) or not _VERSION.match(version):
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
    if not ARCHIVE_NAME.match(filename):
        raise UpstreamError("download response filename is not a plain archive name")
    shasum = parse_sha256(_string(doc, "shasum"))
    if shasum is None:
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
    """One remote configuration's long-lived HTTP session and discovered service.

    ``proxy``, ``proxy_auth`` and ``auth`` are the remote's, which pulpcore passes per
    request rather than on the session.
    """

    stamp: object
    loop: asyncio.AbstractEventLoop
    http: ClientSession
    proxy: str | None
    proxy_auth: BasicAuth | None
    auth: BasicAuth | None
    concurrency: asyncio.Semaphore
    throttler: "_Throttler | None"
    created_at: float = field(default_factory=time.monotonic)
    leases: int = 0
    retired: bool = False
    # Service base URLs found by discovery, keyed by service name.
    services: dict[str, URL] = field(default_factory=dict[str, URL])


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
        except _TRANSPORT_ERRORS:
            self._retire(session)
            raise
        finally:
            # Synchronous, so a canceled request cannot skip it.
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

        # The factory builds the session from the remote's TLS, header and timeout
        # settings; a downloader it builds carries the per-request proxy and auth.
        probe = cast("HttpDownloader", DownloaderFactory(remote).build(remote.url))
        session = _Session(
            stamp=remote.pulp_last_updated,
            loop=loop,
            http=probe.session,
            proxy=probe.proxy,
            proxy_auth=probe.proxy_auth,
            auth=probe.auth,
            concurrency=asyncio.Semaphore(
                remote.download_concurrency or remote.DEFAULT_DOWNLOAD_CONCURRENCY
            ),
            throttler=remote.download_throttler,
        )
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
        return await self._service_url(PROVIDERS_SERVICE)

    async def _service_url(self, service: str) -> URL:
        async with POOL.lease(self._remote) as session:
            known = session.services.get(service)
        if known is not None:
            return known
        discovery = URL(self._remote.url).join(URL(DISCOVERY_PATH))
        document = await self.fetch_json(discovery, max_bytes=MAX_DISCOVERY_BYTES)
        found = parse_discovery(document, discovery, service)
        async with POOL.lease(self._remote) as session:
            session.services[service] = found
        return found

    async def module_versions(self, namespace: str, name: str, system: str) -> list[str]:
        url = await self._module_url(namespace, name, system, "versions")
        return parse_module_versions(await self.fetch_json(url))

    async def module_location(
        self, namespace: str, name: str, system: str, version: str
    ) -> tuple[str, URL]:
        """A module version's go-getter location, and the URL relative locations resolve
        against. Not cached: locations may be signed and short-lived.

        Raises:
            UpstreamNotFound: The registry has no such module version.
            UpstreamError: The answer names no location.
        """
        url = await self._module_url(namespace, name, system, version, "download")
        status, headers, body = await self._fetch_with_retries(str(url), {}, MAX_METADATA_BYTES)
        if status == 404:
            raise UpstreamNotFound(str(url))
        return parse_module_location(status, headers, body), url

    async def _module_url(self, *segments: str) -> URL:
        if not all(_is_segment(segment) for segment in segments):
            raise UpstreamNotFound("/".join(segments))
        return (await self._service_url(MODULES_SERVICE)).joinpath(*segments)

    async def versions(self, address: ProviderAddress) -> list[UpstreamVersion]:
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

    async def fetch_json(self, url: URL, *, max_bytes: int = MAX_METADATA_BYTES) -> object:
        body = await self.fetch_bytes(url, max_bytes=max_bytes)
        try:
            return cast("object", json.loads(body))
        except ValueError as exc:
            raise UpstreamError(f"{url} did not return JSON") from exc

    async def fetch_bytes(self, url: URL | str, *, max_bytes: int = MAX_METADATA_BYTES) -> bytes:
        """Fetch a small document into memory, through the HTTP cache.

        Concurrent fetches of the same URL through the same remote share one request.
        A fresh cached answer, 200 or 404, makes no request; a stale one is revalidated
        and reused on 304. Failures follow ``retry``'s policy, with one exception: the
        first transport failure retries at once on fresh connections, because it usually
        means the upstream reaped an idle pooled connection, not that it is down.

        Raises:
            UpstreamNotFound: The server answered 404, now or within the negative TTL.
            UpstreamUnreachable: The connection kept failing.
            UpstreamError: Any other failure, or a body over ``max_bytes``.
        """
        remote = self._remote
        key = (remote.pk, remote.pulp_last_updated, str(url))
        return await _FETCHES.run(key, lambda: self._fetch_cached(key, str(url), max_bytes))

    async def _fetch_cached(self, key: tuple[object, ...], url: str, max_bytes: int) -> bytes:
        entry = _CACHE.get(key)
        if entry is not None and entry.is_fresh(monotonic()):
            return _answer(entry, url)
        status, headers, body = await self._fetch_with_retries(
            url, entry.validators() if entry else {}, max_bytes
        )
        now = monotonic()
        ceiling = float(self._remote.metadata_ttl)
        if status == 304:
            if entry is None:
                raise UpstreamError(f"{url} answered 304 to an unconditional request")
            if entry.revalidate(headers, now, ceiling=ceiling):
                return _answer(entry, url)
            _CACHE.discard(key)
            return await self._fetch_cached(key, url, max_bytes)
        if status == 204:
            raise UpstreamError(f"{url} answered 204 where a document was expected")
        if status == 404:
            storable, fresh_until = freshness(
                headers, now, default=NEGATIVE_TTL, ceiling=min(ceiling, NEGATIVE_TTL)
            )
        else:
            storable, fresh_until = freshness(headers, now, default=None, ceiling=ceiling)
        result = CacheEntry(
            status=status,
            body=body,
            headers=kept_headers(headers),
            fresh_until=fresh_until,
        )
        if storable:
            _CACHE.put(key, result)
        else:
            _CACHE.discard(key)
        return _answer(result, url)

    async def _fetch_with_retries(
        self, url: str, conditional: Mapping[str, str], max_bytes: int
    ) -> "_Answer":
        max_retries = self._remote.max_retries
        attempts = (DEFAULT_MAX_RETRIES if max_retries is None else max_retries) + 1
        failures = 0
        while True:
            try:
                return await self._fetch_once(url, conditional, max_bytes)
            except ClientResponseError as exc:
                if exc.status == 404:
                    raise UpstreamNotFound(url) from exc
                error: Exception = exc
            except _TRANSPORT_ERRORS as exc:
                error = exc
            failures += 1
            delay = retry_delay(failures, error)
            if failures >= attempts or not is_retryable(error) or delay is None:
                if isinstance(error, ClientResponseError):
                    raise UpstreamError(f"{url} answered {error.status}") from error
                raise UpstreamUnreachable(f"{url} failed: {error!r}") from error
            if failures == 1 and not isinstance(error, ClientResponseError):
                continue
            await asyncio.sleep(delay)

    async def _fetch_once(
        self, url: str, conditional: Mapping[str, str], max_bytes: int
    ) -> "_Answer":
        """One request; 200, 204, 304 and 404 return, any other status raises.

        Other 2xx answers are refused: a 206 or 203 to a plain GET is not the document.
        204 is how a module download answers with only ``X-Terraform-Get``.
        """
        remote = self._remote
        timeout = ClientTimeout(
            total=remote.total_timeout or METADATA_TOTAL_TIMEOUT,
            sock_connect=remote.sock_connect_timeout or METADATA_CONNECT_TIMEOUT,
            sock_read=remote.sock_read_timeout or METADATA_READ_TIMEOUT,
        )
        async with POOL.lease(remote) as session, session.concurrency:
            if session.throttler is not None:
                await session.throttler.acquire()
            async with session.http.get(
                url,
                headers=dict(conditional),
                timeout=timeout,
                proxy=session.proxy,
                proxy_auth=session.proxy_auth,
                auth=session.auth,
            ) as response:
                headers = headers_from_aiohttp_response(response)
                if response.status == 304:
                    return 304, headers, b""
                if response.status == 404:
                    return 404, headers, b""
                if response.status == 204:
                    return 204, headers, b""
                response.raise_for_status()
                if response.status != 200:
                    raise UpstreamError(f"{url} answered {response.status}, not 200")
                # A server that announces or sends more than the limit is cut off unread.
                if (response.content_length or 0) > max_bytes:
                    raise UpstreamError(f"{url} exceeds the {max_bytes}-byte limit")
                body = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    body += chunk
                    if len(body) > max_bytes:
                        raise UpstreamError(f"{url} exceeds the {max_bytes}-byte limit")
                return 200, headers, bytes(body)


def _answer(entry: CacheEntry, url: str) -> bytes:
    if entry.status == 404:
        raise UpstreamNotFound(url)
    return entry.body


type _Answer = tuple[Literal[200, 204, 304, 404], TransportHeaders, bytes]

_FETCHES: SingleFlight[bytes] = SingleFlight()
_CACHE = HttpCache()


def _is_segment(value: str) -> bool:
    """Return whether ``value`` stays one path segment when joined into a URL."""
    return bool(value) and "/" not in value and value not in (".", "..")


# TypeGuard rather than TypeIs: TypeIs needs Python 3.13. json.loads only makes str keys.
def _is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _is_json_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _object(value: object, what: str) -> dict[str, object]:
    if not _is_json_object(value):
        raise UpstreamError(f"{what} is not a JSON object")
    return value


def _list(value: object, what: str) -> list[object]:
    if not _is_json_list(value):
        raise UpstreamError(f"{what} is not a JSON list")
    return value


def _string(doc: dict[str, object], key: str) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value:
        raise UpstreamError(f"{key} is missing or not a string")
    return value


def _strings(value: object, what: str) -> tuple[str, ...]:
    return tuple(item for item in _list(value, what) if isinstance(item, str))


def _platform(doc: dict[str, object]) -> Platform | None:
    os_name, arch = doc.get("os"), doc.get("arch")
    if isinstance(os_name, str) and isinstance(arch, str):
        return Platform.from_key(f"{os_name}_{arch}")
    return None


def _https_url(base_url: URL, value: str) -> str:
    resolved = base_url.join(URL(value))
    if resolved.scheme != "https" or not resolved.host:
        raise UpstreamError("registry URLs must be https")
    return str(resolved)


def _packages(value: object) -> dict[PlatformKey, ReportedPackage]:
    if value is None:
        return {}
    packages: dict[PlatformKey, ReportedPackage] = {}
    for key, raw in _object(value, "packages").items():
        platform = parse_platform_key(key)
        if platform is None:
            continue
        entry = _object(raw, "packages entry")
        zh = None
        for item in _strings(entry.get("hashes", []), "hashes"):
            if item.startswith(ZH_PREFIX):
                zh = parse_sha256(item.removeprefix(ZH_PREFIX)) or zh
        size = entry.get("package_size")
        # bool is an int subclass; JSON true must not read as a size of 1.
        packages[platform.key] = ReportedPackage(
            zh=zh,
            size=size
            if isinstance(size, int) and not isinstance(size, bool) and size > 0
            else None,
        )
    return packages
