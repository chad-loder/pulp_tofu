"""Content, remote, repository and distribution models for OpenTofu providers."""

from functools import partial
from logging import getLogger
from typing import TYPE_CHECKING, Literal, TypedDict, override

from aiohttp import web
from django.db import models
from yarl import URL

from pulpcore.plugin.models import (
    BaseModel,
    Content,
    Distribution,
    Remote,
    Repository,
    RepositoryContent,
)
from pulpcore.plugin.repo_version_utils import remove_duplicates, validate_repo_version
from pulpcore.plugin.util import get_domain, get_domain_pk

from .hashes import Sha256Hex, parse_sha256
from .protocol import Platform, PlatformKey, ProviderAddress
from .signing import Fingerprint, parse_fingerprint

type Policy = Literal["immediate", "on_demand", "streamed"]

if TYPE_CHECKING:
    from pulpcore.plugin.download import DownloaderFactory
    from pulpcore.plugin.models import ContentArtifact, RepositoryVersion, Task

logger = getLogger(__name__)

MIRROR_PATH_PREFIX = "/pulp/tofu/"


class ArchiveRecord(TypedDict):
    """One platform's archive as recorded in ``ProviderVersion.archives``."""

    filename: str
    sha256: Sha256Hex
    size: int | None


def parse_archive_records(value: object) -> dict[PlatformKey, ArchiveRecord]:
    """Restore ``ProviderVersion.archives`` from JSON, dropping malformed entries.

    The column is only written by this plugin, so a dropped entry means a corrupted row.
    """
    records: dict[PlatformKey, ArchiveRecord] = {}
    if not isinstance(value, dict):
        return records
    for key, raw in value.items():
        platform = Platform.from_key(key) if isinstance(key, str) else None
        if platform is None or not isinstance(raw, dict):
            continue
        filename, sha256, size = raw.get("filename"), raw.get("sha256"), raw.get("size")
        digest = parse_sha256(sha256) if isinstance(sha256, str) else None
        if not isinstance(filename, str) or digest is None:
            continue
        records[platform.key] = {
            "filename": filename,
            "sha256": digest,
            "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
        }
    return records


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


class ProviderVersion(Content):
    """The signed checksums for one version of a provider.

    ``shasums`` verified against ``shasums_signature`` when the version was first fetched;
    ``archives`` maps ``os_arch`` to the archive that ``shasums`` lists for it.
    """

    TYPE = "provider_version"
    repo_key_fields = ("hostname", "namespace", "provider_type", "version")  # type: ignore[assignment]

    hostname = models.TextField()
    namespace = models.TextField()
    provider_type = models.TextField()
    version = models.TextField()
    protocols = models.JSONField(default=list)
    shasums = models.BinaryField()
    shasums_signature = models.BinaryField()
    signing_key_fingerprint = models.TextField(blank=True, default="")
    signing_key = models.TextField(blank=True, default="")
    archives = models.JSONField(default=dict)
    _pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.PROTECT)

    @property
    def address(self) -> ProviderAddress:
        """The provider's source address."""
        return ProviderAddress(
            hostname=self.hostname, namespace=self.namespace, type=self.provider_type
        )

    def archive_records(self) -> dict[PlatformKey, ArchiveRecord]:
        """``archives`` as typed records, with entries of the wrong structure dropped."""
        return parse_archive_records(self.archives)

    @property
    def protocol_versions(self) -> tuple[str, ...]:
        """``protocols``, keeping only string elements."""
        return _strings(self.protocols)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        unique_together = ("hostname", "namespace", "provider_type", "version", "_pulp_domain")


class ProviderPackage(Content):
    """One platform's archive of a provider version.

    ``h1`` is empty until Pulp holds the archive and has hashed its contents.
    """

    TYPE = "provider_package"
    repo_key_fields = (  # type: ignore[assignment]
        "hostname",
        "namespace",
        "provider_type",
        "version",
        "os",
        "arch",
    )

    hostname = models.TextField()
    namespace = models.TextField()
    provider_type = models.TextField()
    version = models.TextField()
    os = models.TextField()
    arch = models.TextField()
    filename = models.TextField()
    sha256 = models.CharField(max_length=64)
    size = models.BigIntegerField(null=True)
    h1 = models.TextField(blank=True, default="")
    _pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.PROTECT)

    @property
    def relative_path(self) -> str:
        """The archive's path below a distribution's base path."""
        return (
            f"{self.hostname}/{self.namespace}/{self.provider_type}/{self.version}/{self.filename}"
        )

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        unique_together = (
            "hostname",
            "namespace",
            "provider_type",
            "version",
            "os",
            "arch",
            "sha256",
            "_pulp_domain",
        )


class TofuRemote(Remote):
    """An upstream OpenTofu provider registry.

    ``url`` is the registry's root, where service discovery starts. Downloads follow
    ``policy``: ``on_demand`` stores each archive on first request, ``streamed`` never
    stores archives.
    """

    TYPE = "tofu"

    includes = models.JSONField(default=list)
    excludes = models.JSONField(default=list)
    metadata_ttl = models.PositiveIntegerField(default=3600)
    require_signatures = models.BooleanField(default=False)
    signing_keys = models.JSONField(default=list)

    @property
    def trusted_fingerprints(self) -> frozenset[Fingerprint]:
        """``signing_keys`` fingerprints; empty trusts the keys the registry offers."""
        return frozenset(f for s in _strings(self.signing_keys) if (f := parse_fingerprint(s)))

    @property
    def include_patterns(self) -> tuple[str, ...]:
        """``includes``, keeping only string elements."""
        return _strings(self.includes)

    @property
    def exclude_patterns(self) -> tuple[str, ...]:
        """``excludes``, keeping only string elements."""
        return _strings(self.excludes)

    @property
    def download_policy(self) -> Policy:
        """``policy`` as one of the three values the serializer admits."""
        match self.policy:
            case "immediate" | "on_demand" | "streamed" as policy:
                return policy
            case other:
                raise ValueError(f"unknown remote policy {other!r}")

    @property
    def hostname(self) -> str:
        """The registry hostname as it appears in provider addresses, with a non-443 port."""
        url = URL(self.url)
        host = (url.host or "").lower()
        return f"{host}:{url.explicit_port}" if url.explicit_port not in (None, 443) else host

    @property
    @override
    def download_factory(self) -> "DownloaderFactory":
        """pulpcore's factory, building resuming downloaders for http and https."""
        factory: DownloaderFactory | None = getattr(self, "_download_factory", None)
        if factory is None:
            # Both modules read Django settings on import, so they load after the models.
            from pulpcore.plugin.download import DownloaderFactory as Factory

            from .downloaders import ResumingHttpDownloader

            downloader = partial(ResumingHttpDownloader, total_timeout=self.total_timeout)
            factory = Factory(self, downloader_overrides={"http": downloader, "https": downloader})
            self._download_factory = factory
        return factory

    @override
    def get_remote_artifact_url(
        self, relative_path: str | None = None, request: web.Request | None = None
    ) -> str:
        # Archive URLs come from the upstream download document, never from request paths.
        raise NotImplementedError("pulp_tofu archives carry their upstream URL")

    @override
    def get_remote_artifact_content_type(self, relative_path: str | None = None) -> None:
        return None

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        permissions = [
            ("manage_roles_tofuremote", "Can manage roles on tofu remotes"),
        ]


class ProviderIndex(BaseModel):
    """A remote's cached ``versions`` listing for one provider.

    ``versions`` holds ``{"version", "protocols", "platforms": [{"os", "arch"}]}`` entries.
    """

    remote = models.ForeignKey(TofuRemote, on_delete=models.CASCADE, related_name="indexes")
    hostname = models.TextField()
    namespace = models.TextField()
    provider_type = models.TextField()
    versions = models.JSONField(default=list)
    fetched_at = models.DateTimeField()

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        unique_together = ("remote", "hostname", "namespace", "provider_type")


class TofuRepository(Repository):
    """A repository of provider versions and the archives pulled through for them."""

    TYPE = "tofu"
    CONTENT_TYPES = [ProviderVersion, ProviderPackage]
    REMOTE_TYPES = [TofuRemote]
    PULL_THROUGH_SUPPORTED = True

    @override
    def finalize_new_version(self, new_version: "RepositoryVersion") -> None:
        remove_duplicates(new_version)
        validate_repo_version(new_version)

    @override
    async def async_pull_through_add_content(
        self, content_artifact: "ContentArtifact"
    ) -> "Task | None":
        """Add a pulled package and its provider version in one new repository version."""
        package = await ProviderPackage.objects.filter(
            contentartifact__pk=content_artifact.pk
        ).afirst()
        if package is None:
            return None
        units = [package.pk]
        version = await ProviderVersion.objects.filter(
            hostname=package.hostname,
            namespace=package.namespace,
            provider_type=package.provider_type,
            version=package.version,
            _pulp_domain=get_domain(),
        ).afirst()
        if version is not None:
            units.append(version.pk)
        present = RepositoryContent.objects.filter(
            repository=self, content__in=units, version_removed__isnull=True
        )
        if await present.acount() == len(units):
            return None
        # pulpcore.plugin.tasking loads models on import, so it cannot load with this module.
        from pulpcore.plugin.tasking import aadd_and_remove, adispatch

        body = {"repository_pk": self.pk, "add_content_units": units, "remove_content_units": []}
        task: Task = await adispatch(
            aadd_and_remove, kwargs=body, exclusive_resources=[self], immediate=True
        )
        return task

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        permissions = [
            ("sync_tofurepository", "Can start a sync task"),
            ("modify_tofurepository", "Can modify content of the repository"),
            ("repair_tofurepository", "Can repair repository versions"),
            ("manage_roles_tofurepository", "Can manage roles on tofu repositories"),
        ]


class TofuDistribution(Distribution):
    """Serves the provider network mirror protocol below ``/pulp/tofu/<base_path>/``."""

    TYPE = "tofu"  # type: ignore[assignment]

    @override
    def content_handler(self, path: str) -> web.Response:  # pyright: ignore[reportIncompatibleMethodOverride]
        # The generic content route would proxy arbitrary paths to the remote.
        return web.Response(
            status=404,
            text=f"OpenTofu mirrors are served below {MIRROR_PATH_PREFIX}{self.base_path}/\n",
        )

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        permissions = [
            ("manage_roles_tofudistribution", "Can manage roles on tofu distributions"),
        ]


class TofuNamespace(BaseModel):
    """A registry namespace: the owner of every provider and module published under it.

    The registry answers ``<namespace>/...`` requests from ``distribution``'s repository.
    ``content_hostname`` is the origin hostname of the content served; empty means
    content published to this Pulp, whose origin is ``TOFU_REGISTRY_HOSTNAME``. A
    ``private`` namespace answers only callers with ``view_tofunamespace`` on it.
    """

    name = models.TextField()
    pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.PROTECT)
    distribution = models.ForeignKey(
        TofuDistribution, null=True, on_delete=models.SET_NULL, related_name="namespaces"
    )
    content_hostname = models.TextField(blank=True, default="")
    private = models.BooleanField(default=False)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        unique_together = ("name", "pulp_domain")
        permissions = [
            ("manage_roles_tofunamespace", "Can manage roles on tofu namespaces"),
            ("publish_tofunamespace", "Can publish providers and modules to the namespace"),
        ]
