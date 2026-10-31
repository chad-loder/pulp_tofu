"""REST API serializers for pulp_tofu."""

from gettext import gettext as _
from typing import cast, override

from django.apps import apps
from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers

from pulpcore.plugin import serializers as platform
from pulpcore.plugin.models import Remote

from . import models
from .publishing import MAX_SMALL_FILE_BYTES, ReleaseError, parse_release
from .signing import parse_fingerprint


class _ProviderAddressFields(serializers.Serializer[object]):
    """The fields that name a provider version, shared by both content serializers."""

    FIELDS = ("hostname", "namespace", "provider_type", "version")

    hostname = serializers.CharField(read_only=True, help_text=_("Origin registry hostname."))
    namespace = serializers.CharField(read_only=True, help_text=_("Provider namespace."))
    provider_type = serializers.CharField(read_only=True, help_text=_("Provider type."))
    version = serializers.CharField(read_only=True, help_text=_("Provider version."))


class ProviderVersionSerializer(_ProviderAddressFields, platform.NoArtifactContentSerializer):
    """A provider version and its verified checksums."""

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
            *_ProviderAddressFields.FIELDS,
            "protocols",
            "signing_key_fingerprint",
            "archives",
        )
        model = models.ProviderVersion


class ProviderPackageSerializer(_ProviderAddressFields, platform.SingleArtifactContentSerializer):
    """One platform's archive of a provider version."""

    # pulpcore sets allow_null on the field class, which the schema generator does not
    # read, so generated clients reject the null an archive not yet downloaded has.
    artifact = platform.SingleContentArtifactField(
        allow_null=True,
        help_text=_("The stored archive; null until Pulp has downloaded it."),
    )

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
            *_ProviderAddressFields.FIELDS,
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
        return str(value.mirror_url())


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
    allow_uploads = serializers.BooleanField(
        required=False,
        default=False,
        help_text=_(
            "Accept releases published to the namespaces this distribution serves. A "
            "distribution with a remote cannot accept uploads."
        ),
    )

    @override
    def validate(self, data: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(data)
        instance = self.instance
        remote = attrs.get("remote", getattr(instance, "remote", None))
        uploads = attrs.get("allow_uploads", getattr(instance, "allow_uploads", False))
        # A pull-through cache that is also an upload target would mix upstream releases
        # with in-house ones under the same addresses.
        if remote is not None and uploads:
            raise serializers.ValidationError(
                _("A distribution with a remote cannot accept uploads.")
            )
        return attrs

    class Meta:
        fields = platform.DistributionSerializer.Meta.fields + ("remote", "allow_uploads")
        model = models.TofuDistribution


class TofuNamespaceSerializer(platform.ModelSerializer):
    """A registry namespace and the distribution that serves it."""

    pulp_href = platform.IdentityField(view_name="pulp_tofu/namespaces-detail")
    name = serializers.RegexField(
        r"^[a-z0-9][a-z0-9-]*$",
        max_length=64,
        help_text=_(
            "The namespace in provider source addresses, e.g. acme in pulp.example/acme/widget."
        ),
    )
    distribution = platform.DetailRelatedField(
        required=False,
        allow_null=True,
        view_name_pattern=r"distributions(-.*/.*)?-detail",
        queryset=models.TofuDistribution.objects.all(),
        help_text=_("The distribution whose repository the registry serves for this namespace."),
    )
    content_hostname = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text=_(
            "Origin hostname of the content served, e.g. registry.opentofu.org to serve "
            "synced upstream providers under this Pulp's hostname. Empty serves content "
            "published to this Pulp."
        ),
    )
    private = serializers.BooleanField(
        required=False,
        default=False,
        help_text=_("Answer only callers allowed to view the namespace."),
    )
    keyring = platform.DetailRelatedField(
        required=False,
        allow_null=True,
        view_name_pattern=r"repositories(-.*/.*)?-detail",
        queryset=apps.get_model("core", "OpenPGPKeyring").objects.all(),
        help_text=_("The OpenPGP keyring holding the keys that sign the namespace's releases."),
    )

    def validate_keyring(self, keyring: object) -> object:
        """Refuse a keyring the caller cannot see."""
        user = getattr(self.context.get("request"), "user", None)
        allowed = keyring is None or (
            user is not None
            and (
                user.has_perm("core.view_openpgpkeyring")
                or user.has_perm("core.view_openpgpkeyring", keyring)
            )
        )
        if not allowed:
            raise serializers.ValidationError(_("Unknown keyring."))
        return keyring

    def validate_distribution(
        self, distribution: models.TofuDistribution | None
    ) -> models.TofuDistribution | None:
        """Refuse a distribution the caller cannot see; the namespace would expose it."""
        request = self.context.get("request")
        user = getattr(request, "user", None)
        allowed = distribution is None or (
            user is not None
            and (
                user.has_perm("tofu.view_tofudistribution")
                or user.has_perm("tofu.view_tofudistribution", distribution)
            )
        )
        if not allowed:
            raise serializers.ValidationError(_("Unknown distribution."))
        return distribution

    class Meta:
        fields = platform.ModelSerializer.Meta.fields + (
            "name",
            "distribution",
            "content_hostname",
            "private",
            "keyring",
        )
        model = models.TofuNamespace


class ProviderReleaseSerializer(serializers.Serializer[object]):
    """The files of one provider release, as GoReleaser writes them to ``dist/``."""

    files = serializers.ListField(
        child=serializers.FileField(),
        allow_empty=False,
        help_text=_(
            "The platform zips, SHA256SUMS, SHA256SUMS.sig and the optional manifest.json "
            "of one release, under their release filenames."
        ),
    )

    @override
    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Sort the files into a release; read the small ones, which travel to the task."""
        files = cast("list[UploadedFile]", attrs["files"])
        by_name = {str(f.name): f for f in files}
        try:
            release = parse_release([str(f.name) for f in files])
        except ReleaseError as exc:
            raise serializers.ValidationError({"files": str(exc)}) from exc
        small: dict[str, bytes] = {}
        for name in filter(None, (release.shasums, release.signature, release.manifest)):
            upload = by_name[name]
            if (upload.size or 0) > MAX_SMALL_FILE_BYTES:
                raise serializers.ValidationError({"files": f"{name} is too large"})
            small[name] = upload.read()
        return {"release": release, "small": small, "archives": by_name}
