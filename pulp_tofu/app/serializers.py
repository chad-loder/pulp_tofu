"""REST API serializers for pulp_tofu."""

from gettext import gettext as _
from typing import cast, override

from django.conf import settings
from rest_framework import serializers
from yarl import URL

from pulpcore.plugin import serializers as platform
from pulpcore.plugin.models import Remote

from . import models

_PROVIDER_FIELDS = ("hostname", "namespace", "provider_type", "version")
_POLICIES = frozenset({Remote.ON_DEMAND, Remote.STREAMED})


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
            "streamed serves archives without storing them."
        ),
        choices=[c for c in Remote.POLICY_CHOICES if c[0] in _POLICIES],
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
        )
        model = models.TofuRemote


class TofuRepositorySerializer(platform.RepositorySerializer):
    """A repository of provider versions and their archives."""

    class Meta:
        fields = platform.RepositorySerializer.Meta.fields
        model = models.TofuRepository


class MirrorURLField(serializers.CharField):
    """The network mirror URL for a distribution."""

    @override
    def to_representation(self, value: object) -> str:
        assert isinstance(value, models.TofuDistribution)
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
