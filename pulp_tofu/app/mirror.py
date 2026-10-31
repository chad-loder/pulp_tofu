"""Content-app handler for the provider network mirror protocol.

Each request resolves its distribution by base path, then answers from the
distribution's repository version first and from its remote second. Provider
versions are verified against the upstream signature before they are saved;
archives stream through pulpcore's downloader, which checks them against the signed
SHA-256 while it saves them.
"""

import asyncio
import logging
import weakref
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from aiohttp import web
from asgiref.sync import sync_to_async
from django.db import IntegrityError
from django.utils import timezone

from pulpcore.plugin.content import Handler, PathNotResolved
from pulpcore.plugin.models import ContentArtifact, RemoteArtifact, RepositoryVersion
from pulpcore.plugin.tasking import adispatch
from pulpcore.plugin.util import get_domain, get_domain_pk

from .flight import SingleFlight
from .hashes import zh_hash
from .models import (
    ArchiveRecord,
    ProviderIndex,
    ProviderPackage,
    ProviderVersion,
    TofuDistribution,
    TofuRemote,
    TofuRepository,
)
from .protocol import (
    JSON_CONTENT_TYPE,
    ArchiveEntry,
    Platform,
    ProviderAddress,
    address_allowed,
    archive_url,
    index_document,
    protocols_supported,
    version_document,
)
from .registry import (
    RegistryClient,
    UpstreamError,
    UpstreamNotFound,
    UpstreamVersion,
    parse_versions,
)
from .signing import SignatureError
from .tasks import record_h1
from .verify import fetch_provider_version, platform_of

if TYPE_CHECKING:
    from django.db.models import QuerySet

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Source:
    distribution: TofuDistribution
    remote: TofuRemote | None
    repository: TofuRepository | None
    repository_version: RepositoryVersion | None
    address: ProviderAddress

    @property
    def upstream(self) -> TofuRemote | None:
        """The remote, when it may fetch this provider."""
        remote = self.remote
        if remote is None or remote.hostname != self.address.hostname:
            return None
        if not address_allowed(self.address, remote.include_patterns, remote.exclude_patterns):
            return None
        return remote


class TofuMirror(Handler):
    """Answers ``index.json``, ``:version.json`` and archive requests."""

    distribution_model = TofuDistribution  # type: ignore[assignment]

    async def index(self, request: web.Request) -> web.StreamResponse:
        """``GET :hostname/:namespace/:type/index.json``."""
        source = await self._source(request)
        versions: set[str] = set()
        if source.repository_version is not None:
            local = self._local_versions(source).values_list("version", flat=True)
            versions.update([v async for v in local])
        if (remote := source.upstream) is not None:
            try:
                listing = await self._listing(remote, source.address)
            except web.HTTPBadGateway:
                if not versions:
                    raise
                listing = []  # The repository still answers while upstream is down.
            versions.update(v.version for v in listing if protocols_supported(v.protocols))
        if not versions:
            raise PathNotResolved(request.path)
        return _json(index_document(sorted(versions)))

    async def version(self, request: web.Request) -> web.StreamResponse:
        """``GET :hostname/:namespace/:type/:version.json``."""
        source = await self._source(request)
        provider_version = await self._provider_version(source, request.match_info["version"])
        h1s = {
            (Platform(os=p.os, arch=p.arch).key, p.sha256): p.h1
            async for p in ProviderPackage.objects.filter(
                **_address_filter(source.address),
                version=provider_version.version,
                _pulp_domain=get_domain(),
            ).exclude(h1="")
        }
        archives: dict[str, ArchiveEntry] = {}
        for key, record in provider_version.archive_records().items():
            hashes: list[str] = [zh_hash(record["sha256"])]
            if h1 := h1s.get((key, record["sha256"])):
                hashes.append(h1)
            archives[key] = {
                "url": archive_url(provider_version.version, record["filename"]),
                "hashes": hashes,
            }
        return _json(version_document(archives))

    async def checksums(self, request: web.Request) -> web.StreamResponse:
        """``GET :hostname/:namespace/:type/:version/_checksums/SHA256SUMS[.sig]``.

        The registry protocol's download documents point here. The bytes are the ones
        the signature was verified over, served unchanged.
        """
        source = await self._source(request)
        provider_version = await self._provider_version(source, request.match_info["version"])
        name = request.match_info["name"]
        if name == "SHA256SUMS":
            return web.Response(body=bytes(provider_version.shasums), content_type="text/plain")
        if name == "SHA256SUMS.sig" and provider_version.shasums_signature:
            return web.Response(
                body=bytes(provider_version.shasums_signature),
                content_type="application/octet-stream",
            )
        raise PathNotResolved(request.path)

    async def archive(self, request: web.Request) -> web.StreamResponse:
        """``GET :hostname/:namespace/:type/:version/:filename``."""
        source = await self._source(request)
        provider_version = await self._provider_version(source, request.match_info["version"])
        filename = request.match_info["filename"]
        found = _archive_for(provider_version, filename)
        if found is None:
            raise PathNotResolved(request.path)
        platform, record = found
        package = await self._package(provider_version, platform, record)
        relative_path = package.relative_path
        headers = self.response_headers(relative_path, source.distribution)

        stored = await _stored_archive(package)
        if stored is not None:
            if source.repository is not None:
                await source.repository.async_pull_through_add_content(stored)
            return await self._serve_content_artifact(stored, headers, request)

        remote = source.upstream
        if remote is None:
            raise PathNotResolved(request.path)
        if remote.download_policy == "streamed":
            # Nothing is stored for waiters to share, so each request streams its own copy.
            return await self._pull(request, headers, remote, source, package, platform, record)
        # One request per archive downloads it; the others wait, then serve what it stored.
        async with _archive_lock(package.pk):
            stored = await _stored_archive(package)
            if stored is not None:
                return await self._serve_content_artifact(stored, headers, request)
            response = await self._pull(request, headers, remote, source, package, platform, record)
        await adispatch(record_h1, kwargs={"package_pk": str(package.pk)})
        return response

    async def _pull(
        self,
        request: web.Request,
        headers: "Mapping[str, str]",
        remote: TofuRemote,
        source: _Source,
        package: ProviderPackage,
        platform: Platform,
        record: ArchiveRecord,
    ) -> web.StreamResponse:
        """Stream the archive from upstream to the client, storing it unless streamed."""
        remote_artifact = await self._remote_artifact(remote, source, package, platform, record)
        response: web.StreamResponse = await self._stream_remote_artifact(
            request,
            web.StreamResponse(headers=headers),
            remote_artifact,
            save_artifact=remote.download_policy != "streamed",
            repository=source.repository,
        )
        return response

    async def _source(self, request: web.Request) -> _Source:
        match = request.match_info
        # Handler._stream_remote_artifact logs match_info["path"], which these routes lack.
        match["path"] = request.path
        distribution = await (
            TofuDistribution.objects.select_related("remote", "content_guard")
            .filter(pulp_domain=get_domain(), base_path=match["base_path"])
            .afirst()
        )
        if distribution is None:
            raise PathNotResolved(request.path)
        await sync_to_async(self._permit)(request, distribution)
        repository, repository_version, _ = await sync_to_async(
            distribution.get_repository_publication_and_version
        )()
        base_remote = distribution.remote
        remote = await base_remote.acast() if base_remote is not None else None
        return _Source(
            distribution=distribution,
            remote=remote,
            repository=repository,
            repository_version=repository_version,
            address=ProviderAddress(
                hostname=match["hostname"],
                namespace=match["namespace"],
                type=match["provider_type"],
            ),
        )

    @staticmethod
    def _local_versions(source: _Source) -> "QuerySet[ProviderVersion]":
        if source.repository_version is None:
            return ProviderVersion.objects.none()
        return ProviderVersion.objects.filter(
            pk__in=source.repository_version.content, **_address_filter(source.address)
        )

    async def _listing(
        self, remote: TofuRemote, address: ProviderAddress, *, wanted: str | None = None
    ) -> list[UpstreamVersion]:
        """The provider's upstream versions, answered from the cache whenever it has them.

        A stale listing is returned at once and refreshed in the background. Only a
        provider never listed before, or a ``wanted`` version the cached listing lacks,
        waits for upstream. Upstream 404s are cached for ``NEGATIVE_TTL`` at most.
        """
        cached = await ProviderIndex.objects.filter(
            remote=remote, **_address_filter(address)
        ).afirst()
        if cached is None:
            return await _refresh_listing(remote, address)
        listing = _versions_from_json(cached.versions)
        age = timezone.now() - cached.fetched_at
        ttl = remote.metadata_ttl if listing else min(remote.metadata_ttl, NEGATIVE_TTL)
        missing = wanted is not None and all(v.version != wanted for v in listing)
        if missing and age > timedelta(seconds=NEGATIVE_TTL):
            try:
                return await _refresh_listing(remote, address)
            except web.HTTPBadGateway:
                return listing
        if age > timedelta(seconds=ttl):
            _in_background(_refresh_listing(remote, address), f"refreshing {address}")
        return listing

    async def _provider_version(self, source: _Source, version: str) -> ProviderVersion:
        """The verified checksums for ``version``, fetching them upstream on first use."""
        if source.repository_version is not None:
            local = await self._local_versions(source).filter(version=version).afirst()
            if local is not None:
                return local
        remote = source.upstream
        if remote is None:
            raise PathNotResolved(str(source.address))
        existing = await ProviderVersion.objects.filter(
            **_address_filter(source.address), version=version, _pulp_domain=get_domain()
        ).afirst()
        if existing is not None:
            if remote.require_signatures and not existing.signing_key_fingerprint:
                raise web.HTTPBadGateway(reason=f"{source.address} {version} is unsigned")
            return existing
        listing = await self._listing(remote, source.address, wanted=version)
        upstream = next((v for v in listing if v.version == version), None)
        if upstream is None or not protocols_supported(upstream.protocols):
            raise PathNotResolved(f"{source.address} {version}")
        listed: UpstreamVersion = upstream
        key = (get_domain_pk(), str(source.address), version)
        return await _VERSIONS.run(key, lambda: _verify_and_store(remote, source.address, listed))

    @staticmethod
    async def _package(
        provider_version: ProviderVersion, platform: Platform, record: ArchiveRecord
    ) -> ProviderPackage:
        """The package for one platform, created without an artifact on first use."""
        fields = {
            "hostname": provider_version.hostname,
            "namespace": provider_version.namespace,
            "provider_type": provider_version.provider_type,
            "version": provider_version.version,
            "os": platform.os,
            "arch": platform.arch,
            "sha256": record["sha256"],
            "_pulp_domain": get_domain(),
        }
        package = await ProviderPackage.objects.filter(**fields).afirst()
        if package is None:
            package = ProviderPackage(**fields, filename=record["filename"], size=record["size"])
            try:
                await package.asave()
            except IntegrityError:
                package = await ProviderPackage.objects.aget(**fields)
        try:
            await ContentArtifact.objects.aget_or_create(
                content=package, relative_path=package.relative_path
            )
        except IntegrityError:
            pass  # A concurrent request created it.
        return package

    @staticmethod
    async def _remote_artifact(
        remote: TofuRemote,
        source: _Source,
        package: ProviderPackage,
        platform: Platform,
        record: ArchiveRecord,
    ) -> RemoteArtifact:
        """This remote's download of ``package``, checked against the signed SHA-256."""
        content_artifact = await ContentArtifact.objects.aget(
            content=package, relative_path=package.relative_path
        )
        existing = (
            await RemoteArtifact.objects.select_related("remote")
            .filter(content_artifact=content_artifact, remote=remote)
            .afirst()
        )
        if existing is not None:
            return existing
        try:
            info = await RegistryClient(remote).download(source.address, package.version, platform)
        except UpstreamNotFound as exc:
            raise PathNotResolved(str(exc)) from exc
        except UpstreamError as exc:
            raise web.HTTPBadGateway(reason=str(exc)) from exc
        if info.filename != record["filename"] or info.shasum != record["sha256"]:
            raise web.HTTPBadGateway(reason="download document disagrees with signed SHA256SUMS")
        remote_artifact = RemoteArtifact(
            url=info.download_url,
            sha256=record["sha256"],
            size=record["size"],
            content_artifact=content_artifact,
            remote=remote,
        )
        try:
            await remote_artifact.asave()
        except IntegrityError:
            return await RemoteArtifact.objects.select_related("remote").aget(
                content_artifact=content_artifact, remote=remote
            )
        return remote_artifact


async def _refresh_listing(remote: TofuRemote, address: ProviderAddress) -> list[UpstreamVersion]:
    """Fetch a provider's versions and cache them; concurrent callers share one fetch.

    An upstream 404 is cached as an empty listing. Raises ``HTTPBadGateway`` when the
    upstream fails; callers holding a stale listing serve that instead.
    """
    key = (remote.pk, str(address))
    return await _LISTINGS.run(key, lambda: _fetch_and_store_listing(remote, address))


async def _fetch_and_store_listing(
    remote: TofuRemote, address: ProviderAddress
) -> list[UpstreamVersion]:
    try:
        listing = await RegistryClient(remote).versions(address)
    except UpstreamNotFound:
        listing = []
    except UpstreamError as exc:
        log.warning("Listing %s failed: %s", address, exc)
        raise web.HTTPBadGateway(reason=str(exc)) from exc
    try:
        await ProviderIndex.objects.aupdate_or_create(
            remote=remote,
            hostname=address.hostname,
            namespace=address.namespace,
            provider_type=address.type,
            defaults={"versions": _versions_to_json(listing), "fetched_at": timezone.now()},
        )
    except IntegrityError:
        pass  # Another worker stored the same listing.
    return listing


async def _verify_and_store(
    remote: TofuRemote, address: ProviderAddress, upstream: UpstreamVersion
) -> ProviderVersion:
    """Verify a version's checksums upstream and save them, or return the saved row."""
    try:
        provider_version = await fetch_provider_version(remote, address, upstream)
    except UpstreamNotFound as exc:
        raise PathNotResolved(str(exc)) from exc
    except (UpstreamError, SignatureError) as exc:
        log.warning("Refusing %s %s: %s", address, upstream.version, exc)
        raise web.HTTPBadGateway(reason=str(exc)) from exc
    try:
        await provider_version.asave()
    except IntegrityError:
        return await ProviderVersion.objects.aget(
            **_address_filter(address), version=upstream.version, _pulp_domain=get_domain()
        )
    return provider_version


def _in_background(work: "Coroutine[object, object, object]", what: str) -> None:
    """Run ``work`` detached from the request; log, never raise, its failure."""
    task = asyncio.ensure_future(work)
    _BACKGROUND.add(task)

    def finished(done: "asyncio.Future[object]") -> None:
        _BACKGROUND.discard(task)
        if not done.cancelled() and (exc := done.exception()) is not None:
            log.warning("Background %s failed: %s", what, exc)

    task.add_done_callback(finished)


async def _stored_archive(package: ProviderPackage) -> ContentArtifact | None:
    """The package's content artifact, if Pulp holds its archive."""
    stored: ContentArtifact | None = (
        await ContentArtifact.objects.select_related("artifact")
        .filter(content=package, artifact__isnull=False)
        .afirst()
    )
    return stored


def _archive_lock(package_pk: object) -> asyncio.Lock:
    """The lock that serializes first downloads of one package within this process."""
    lock = _ARCHIVE_LOCKS.get(package_pk)
    if lock is None:
        lock = asyncio.Lock()
        _ARCHIVE_LOCKS[package_pk] = lock
    return lock


# Seconds an upstream "no such provider" or a missing version stays cached.
NEGATIVE_TTL = 60

_LISTINGS: SingleFlight[list[UpstreamVersion]] = SingleFlight()
_VERSIONS: SingleFlight[ProviderVersion] = SingleFlight()
_BACKGROUND: set["asyncio.Task[object]"] = set()
# Weak values: a lock lives only while some request holds or awaits it.
_ARCHIVE_LOCKS: "weakref.WeakValueDictionary[object, asyncio.Lock]" = weakref.WeakValueDictionary()


def _archive_for(
    provider_version: ProviderVersion, filename: str
) -> tuple[Platform, ArchiveRecord] | None:
    for key, record in provider_version.archive_records().items():
        if record["filename"] == filename:
            return platform_of(key), record
    return None


def _address_filter(address: ProviderAddress) -> dict[str, str]:
    return {
        "hostname": address.hostname,
        "namespace": address.namespace,
        "provider_type": address.type,
    }


def _versions_to_json(listing: list[UpstreamVersion]) -> list[dict[str, object]]:
    return [
        {
            "version": v.version,
            "protocols": list(v.protocols),
            "platforms": [{"os": p.os, "arch": p.arch} for p in v.platforms],
        }
        for v in listing
    ]


def _versions_from_json(stored: list[dict[str, object]]) -> list[UpstreamVersion]:
    return parse_versions({"versions": stored})


def _json(body: bytes) -> web.Response:
    return web.Response(body=body, content_type=JSON_CONTENT_TYPE)
