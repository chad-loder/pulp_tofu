"""Sync: fetch chosen providers from a remote into a new repository version."""

import logging
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass
from typing import TypedDict, Unpack, override

from pulpcore.plugin.exceptions import SyncError
from pulpcore.plugin.models import Artifact, ProgressReport
from pulpcore.plugin.stages import (
    DeclarativeArtifact,
    DeclarativeContent,
    DeclarativeVersion,
    Stage,
)

from pulp_tofu.app.models import (
    ArchiveRecord,
    ProviderPackage,
    ProviderVersion,
    TofuRemote,
    TofuRepository,
)
from pulp_tofu.app.protocol import (
    Platform,
    PlatformKey,
    ProviderAddress,
    address_allowed,
    protocols_supported,
)
from pulp_tofu.app.registry import RegistryClient, UpstreamError, UpstreamVersion
from pulp_tofu.app.signing import SignatureError
from pulp_tofu.app.tasks.hashing import record_h1
from pulp_tofu.app.verify import fetch_provider_version, platform_of
from pulp_tofu.app.versions import latest_releases

log = logging.getLogger(__name__)


class ProviderSelection(TypedDict):
    """One provider to sync. ``latest`` counts releases when ``versions`` is empty."""

    namespace: str
    type: str
    versions: list[str]
    latest: int


class SyncKwargs(TypedDict):
    """The sync task's keyword arguments, as the viewset dispatches them."""

    remote_pk: str
    repository_pk: str
    providers: list[ProviderSelection]
    platforms: list[str]
    mirror: bool


def synchronize(**kwargs: Unpack[SyncKwargs]) -> None:
    """Create a repository version holding the selected providers.

    An empty ``versions`` selects the provider's ``latest`` releases; an empty
    ``platforms`` selects every platform. Archives download now under the ``immediate``
    policy and on first request otherwise.
    """
    remote = TofuRemote.objects.get(pk=kwargs["remote_pk"])
    repository = TofuRepository.objects.get(pk=kwargs["repository_pk"])
    providers = [_selection(raw) for raw in kwargs["providers"]]
    platforms = frozenset(
        p.key for raw in kwargs["platforms"] if (p := Platform.from_key(raw)) is not None
    )
    mirror = kwargs["mirror"]
    first_stage = ProviderStage(remote, providers, platforms)
    version = DeclarativeVersion(first_stage, repository, mirror=mirror).create()
    if version is None:
        return
    unhashed = ProviderPackage.objects.filter(
        pk__in=version.content, h1="", contentartifact__artifact__isnull=False
    ).values_list("pk", flat=True)
    for package_pk in unhashed:
        record_h1(str(package_pk))


def _selection(raw: object) -> ProviderSelection:
    """Check one provider selection from the task's JSON-deserialized kwargs."""
    if not isinstance(raw, dict):
        raise SyncError("a provider selection is not an object")
    namespace, provider_type = raw.get("namespace"), raw.get("type")
    versions, latest = raw.get("versions", []), raw.get("latest", 1)
    if not (isinstance(namespace, str) and isinstance(provider_type, str)):
        raise SyncError("a provider selection lacks namespace or type")
    if not (isinstance(versions, list) and all(isinstance(v, str) for v in versions)):
        raise SyncError(f"{namespace}/{provider_type}: versions must be strings")
    if not isinstance(latest, int) or isinstance(latest, bool) or latest < 1:
        raise SyncError(f"{namespace}/{provider_type}: latest must be a positive integer")
    return {"namespace": namespace, "type": provider_type, "versions": versions, "latest": latest}


@dataclass(frozen=True, slots=True, kw_only=True)
class _Selected:
    address: ProviderAddress
    upstream: UpstreamVersion


class ProviderStage(Stage):
    """Emits each selected provider version and its platform archives."""

    def __init__(
        self,
        remote: TofuRemote,
        providers: list[ProviderSelection],
        platforms: frozenset[PlatformKey],
    ) -> None:
        super().__init__()
        self._remote = remote
        self._providers = providers
        self._platforms = platforms

    @override
    async def run(self) -> None:
        client = RegistryClient(self._remote)
        selected = [s async for s in self._select(client)]
        async with ProgressReport(
            message="Verifying provider versions",
            code="sync.verifying.provider_versions",
            total=len(selected),
        ) as progress:
            for item in selected:
                await self._emit(client, item)
                await progress.aincrement()

    async def _select(self, client: RegistryClient) -> AsyncGenerator[_Selected]:
        remote = self._remote
        for wanted in self._providers:
            address = ProviderAddress(
                hostname=remote.hostname, namespace=wanted["namespace"], type=wanted["type"]
            )
            if not address_allowed(address, remote.include_patterns, remote.exclude_patterns):
                raise SyncError(f"{address} is excluded by the remote's filters")
            try:
                listing = await client.versions(address)
            except UpstreamError as exc:
                raise SyncError(f"{address}: {exc}") from exc
            available = {v.version: v for v in listing if protocols_supported(v.protocols)}
            versions = wanted["versions"] or latest_releases(list(available), wanted["latest"])
            if not versions:
                raise SyncError(f"{address} has no installable release")
            missing = [v for v in versions if v not in available]
            if missing:
                raise SyncError(f"{address} has no installable version {', '.join(missing)}")
            for version in versions:
                yield _Selected(address=address, upstream=available[version])

    async def _emit(self, client: RegistryClient, item: _Selected) -> None:
        remote = self._remote
        try:
            provider_version = await fetch_provider_version(remote, item.address, item.upstream)
        except (UpstreamError, SignatureError) as exc:
            raise SyncError(f"{item.address} {item.upstream.version}: {exc}") from exc
        await self.put(DeclarativeContent(content=provider_version))
        for key, record in _wanted_archives(provider_version, self._platforms):
            platform = platform_of(key)
            try:
                info = await client.download(item.address, provider_version.version, platform)
            except UpstreamError as exc:
                raise SyncError(f"{item.address} {provider_version.version} {key}: {exc}") from exc
            if info.filename != record["filename"] or info.shasum != record["sha256"]:
                raise SyncError(
                    f"{item.address} {provider_version.version} {key}: "
                    "download document disagrees with signed SHA256SUMS"
                )
            package = ProviderPackage(
                hostname=provider_version.hostname,
                namespace=provider_version.namespace,
                provider_type=provider_version.provider_type,
                version=provider_version.version,
                os=platform.os,
                arch=platform.arch,
                filename=record["filename"],
                sha256=record["sha256"],
                size=record["size"],
            )
            # Size is only known when the registry reported it.
            expected = Artifact(sha256=record["sha256"])
            if record["size"] is not None:
                expected.size = record["size"]
            artifact = DeclarativeArtifact(
                artifact=expected,
                url=info.download_url,
                relative_path=package.relative_path,
                remote=remote,
                deferred_download=remote.download_policy != "immediate",
            )
            await self.put(DeclarativeContent(content=package, d_artifacts=[artifact]))


def _wanted_archives(
    provider_version: ProviderVersion, platforms: frozenset[PlatformKey]
) -> Iterator[tuple[PlatformKey, ArchiveRecord]]:
    for key, record in sorted(provider_version.archive_records().items()):
        if not platforms or key in platforms:
            yield key, record
