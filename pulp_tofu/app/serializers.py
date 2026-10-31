"""REST API serializers for pulp_tofu."""

from gettext import gettext as _
from typing import cast, override

from django.conf import settings
from rest_framework import serializers
from yarl import URL

from pulpcore.plugin import serializers as platform
from pulpcore.plugin.models import Remote

from . import models
from .signing import parse_fingerprint

_PROVIDER_FIELDS = ("hostname", "namespace", "provider_type", "version")


class ProviderVersionSerializer(platform.NoArtifactContentSerializer):
    """A provider version and its verified checksums."""

    hostname = serializers.CharField(read_only=True, help_text=_("Origin registry hostname."))
    namespace = serializers.CharField(read_only=True, help_text=_("Provider namespace."))
    provider_type = serializers.CharField(read_only=True, help_text=_("Provider type."))
    version = serializers.CharField(read_only=True, help_text=_("Provider version."))
    protocols = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        help_text=_("Plugin protocol versions the provider supports."),
    )
    signing_key_fingerprint = serializers.CharField(
        read_only=True,
        help_text=_("Fingerprint of the OpenPGP key that signed the checksums; empty if unsigned."),
    )
    archives = serializers.JSONField(
        read_only=True,
        help_text=_("Archive filename, SHA-256 and size for each os_arch platform."),
    )

    class Meta:
        fields = platform.NoArtifactContentSerializer.Meta.fields + (
            *_PROVIDER_FIELDS,
            "protocols",
            "signing_key_fingerprint",
            "archives",
        )
        model = models.ProviderVersion


class ProviderPackageSerializer(platform.SingleArtifactContentSerializer):
    """One platform's archive of a provider version."""

    # pulpcore sets allow_null on the field class, which the schema generator does not
    # read, so generated clients reject the null an archive not yet downloaded has.
    artifact = platform.SingleContentArtifactField(
        allow_null=True,
        help_text=_("The stored archive; null until Pulp has downloaded it."),
    )

    hostname = serializers.CharField(read_only=True, help_text=_("Origin registry hostname."))
    namespace = serializers.CharField(read_only=True, help_text=_("Provider namespace."))
    provider_type = serializers.CharField(read_only=True, help_text=_("Provider type."))
    version = serializers.CharField(read_only=True, help_text=_("Provider version."))
    os = serializers.CharField(read_only=True, help_text=_("Operating system."))
    arch = serializers.CharField(read_only=True, help_text=_("CPU architecture."))
    filename = serializers.CharField(read_only=True, help_text=_("Archive filename."))
    sha256 = serializers.CharField(read_only=True, help_text=_("SHA-256 of the archive."))
    size = serializers.IntegerField(read_only=True, allow_null=True, help_text=_("Bytes."))
    h1 = serializers.CharField(
        read_only=True, help_text=_("OpenTofu h1: hash; empty until the archive is stored.")
    )

    class Meta:
        fields = platform.SingleArtifactContentSerializer.Meta.fields + (
            *_PROVIDER_FIELDS,
            "os",
            "arch",
            "filename",
            "sha256",
            "size",
            "h1",
        )
        model = models.ProviderPackage


class TofuRemoteSerializer(platform.RemoteSerializer):
    """An upstream OpenTofu provider registry."""

    url = serializers.CharField(
        help_text=_(
            "The registry root, where service discovery starts, e.g. "
            "https://registry.opentofu.org/."
        )
    )
    policy = serializers.ChoiceField(
        help_text=_(
            "on_demand stores each archive the first time a client requests it; "
            "streamed serves archives without storing them; immediate also downloads "
            "every archive a sync selects."
        ),
        choices=Remote.POLICY_CHOICES,
        default=Remote.ON_DEMAND,
    )
    includes = serializers.ListField(
        child=serializers.CharField(max_length=255),
        required=False,
        default=list,
        help_text=_(
            "hostname/namespace/type glob patterns the remote may fetch; empty admits all."
        ),
    )
    excludes = serializers.ListField(
        child=serializers.CharField(max_length=255),
        required=False,
        default=list,
        help_text=_("hostname/namespace/type glob patterns the remote must not fetch."),
    )
    metadata_ttl = serializers.IntegerField(
        min_value=0,
        required=False,
        default=3600,
        help_text=_("Seconds a provider's upstream version list is reused before refreshing."),
    )
    require_signatures = serializers.BooleanField(
        required=False,
        default=False,
        help_text=_(
            "Refuse unsigned providers from registry.opentofu.org. Providers from any "
            "other registry must always be signed."
        ),
    )
    signing_keys = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text=_(
            "OpenPGP fingerprints whose signatures this remote accepts. Empty accepts "
            "the keys the registry publishes with each provider; set, it refuses any "
            "other key, including unsigned providers."
        ),
    )

    def validate_signing_keys(self, value: list[str]) -> list[str]:
        """Normalize fingerprints to lowercase hex without spaces."""
        fingerprints: list[str] = []
        for raw in value:
            fingerprint = parse_fingerprint(raw)
            if fingerprint is None:
                raise serializers.ValidationError(_("Not an OpenPGP fingerprint: {}").format(raw))
            fingerprints.append(fingerprint)
        return fingerprints

    @override
    def validate_url(self, url: str) -> str:
        """Require an https registry root ending in a slash."""
        url = super().validate_url(url)
        if not url.startswith("https://"):
            raise serializers.ValidationError(_("The registry URL must use https."))
        return url if url.endswith("/") else f"{url}/"

    @override
    def validate(self, data: dict[str, object]) -> dict[str, object]:
        """Reject credentials, which pulpcore would also send to archive download hosts."""
        data = cast("dict[str, object]", super().validate(data))
        if data.get("username") or data.get("password"):
            raise serializers.ValidationError(
                _("Registry credentials are not supported; archive downloads would carry them.")
            )
        return data

    class Meta:
        fields = platform.RemoteSerializer.Meta.fields + (
            "includes",
            "excludes",
            "metadata_ttl",
            "require_signatures",
            "signing_keys",
        )
        model = models.TofuRemote


class TofuRepositorySerializer(platform.RepositorySerializer):
    """A repository of provider versions and their archives."""

    class Meta:
        fields = platform.RepositorySerializer.Meta.fields
        model = models.TofuRepository


class ProviderSelectionSerializer(serializers.Serializer[dict[str, object]]):
    """One provider for a sync to fetch from the remote's registry."""

    namespace = serializers.CharField(help_text=_("Provider namespace, e.g. hashicorp."))
    type = serializers.CharField(help_text=_("Provider type, e.g. aws."))
    versions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text=_("Exact versions to fetch; empty fetches the latest releases."),
    )
    latest = serializers.IntegerField(
        min_value=1,
        max_value=100,
        required=False,
        default=1,
        help_text=_("How many of the newest releases to fetch when versions is empty."),
    )


class TofuSyncSerializer(platform.RepositorySyncURLSerializer):
    """Sync options: which providers and platforms to fetch."""

    providers = ProviderSelectionSerializer(
        many=True,
        allow_empty=False,
        help_text=_("Providers to fetch; the registry offers no list of all providers."),
    )
    platforms = serializers.ListField(
        child=serializers.RegexField(r"^[a-z0-9]+_[a-z0-9]+$"),
        required=False,
        default=list,
        help_text=_("os_arch platforms to fetch, e.g. linux_amd64; empty fetches all."),
    )


class MirrorURLField(serializers.CharField):
    """The network mirror URL for a distribution."""

    @override
    def to_representation(self, value: object) -> str:
        if not isinstance(value, models.TofuDistribution):
            raise TypeError(f"MirrorURLField serializes distributions, not {type(value)}")
        # Same origin handling as pulpcore's BaseURLField; CONTENT_ORIGIN may be unset.
        origin = cast("str | None", settings.CONTENT_ORIGIN)
        url = URL((origin or "").rstrip("/") + models.MIRROR_PATH_PREFIX)
        if settings.DOMAIN_ENABLED:
            url = url / value.pulp_domain.name / ""
        return str(url.joinpath(*value.base_path.strip("/").split("/"), ""))


class TofuDistributionSerializer(platform.DistributionSerializer):
    """Serves a repository and a remote as an OpenTofu provider network mirror."""

    base_url = MirrorURLField(  # type: ignore[assignment]
        source="*",
        read_only=True,
        help_text=_("The URL to set as network_mirror.url in the OpenTofu CLI configuration."),
    )
    remote = platform.DetailRelatedField(
        required=False,
        help_text=_("Remote that fetches providers not yet in Pulp."),
        view_name_pattern=r"remotes(-.*/.*)?-detail",
        queryset=models.TofuRemote.objects.all(),
        allow_null=True,
    )

    class Meta:
        fields = platform.DistributionSerializer.Meta.fields + ("remote",)
        model = models.TofuDistribution
