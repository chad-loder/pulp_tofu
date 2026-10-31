"""REST API serializers for pulp_tofu."""

from dataclasses import dataclass
from datetime import datetime
from gettext import gettext as _
from typing import cast, override

from django.apps import apps
from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers
from yarl import URL

from pulpcore.plugin import serializers as platform
from pulpcore.plugin.models import Remote

from . import models
from .authentication import IssuedToken, issue_token
from .protocol import (
    Platform,
    is_module_name,
    is_module_system,
    is_provider_part,
    normalize_hostname,
)
from .publishing import MAX_SMALL_FILE_BYTES, ReleaseError, ReleaseFiles, parse_release
from .signing import parse_fingerprint
from .versions import is_release_version


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


class ModuleVersionSerializer(platform.SingleArtifactContentSerializer):
    """One published version of a module package."""

    hostname = serializers.CharField(read_only=True, help_text=_("Origin registry hostname."))
    namespace = serializers.CharField(read_only=True, help_text=_("Module namespace."))
    name = serializers.CharField(read_only=True, help_text=_("Module name."))
    system = serializers.CharField(read_only=True, help_text=_("Target system, e.g. aws."))
    version = serializers.CharField(read_only=True, help_text=_("Module version."))
    sha256 = serializers.CharField(read_only=True, help_text=_("SHA-256 of the package."))
    size = serializers.IntegerField(read_only=True, help_text=_("Bytes."))

    class Meta:
        fields = platform.SingleArtifactContentSerializer.Meta.fields + (
            "hostname",
            "namespace",
            "name",
            "system",
            "version",
            "sha256",
            "size",
        )
        model = models.ModuleVersion


class ModuleReleaseSerializer(serializers.Serializer[object]):
    """One version of a module package to publish."""

    name = serializers.CharField(help_text=_("Module name, e.g. vpc in pulp.example/acme/vpc/aws."))
    system = serializers.CharField(help_text=_("Target system, e.g. aws."))
    version = serializers.CharField(help_text=_("SemVer 2.0 version without build metadata."))
    file = serializers.FileField(help_text=_("A .tar.gz of the module directory."))

    def validate_name(self, name: str) -> str:
        if not is_module_name(name):
            raise serializers.ValidationError(_("OpenTofu does not accept this module name."))
        return name

    def validate_system(self, system: str) -> str:
        if not is_module_system(system):
            raise serializers.ValidationError(_("OpenTofu does not accept this target system."))
        return system

    def validate_version(self, version: str) -> str:
        if not is_release_version(version):
            raise serializers.ValidationError(
                _("Expected a SemVer 2.0 version without build metadata.")
            )
        return version

    def uploaded_module(self) -> "UploadedModule":
        """The validated upload; call after ``is_valid``."""
        data = self.validated_data
        upload = data["file"]
        if not isinstance(upload, UploadedFile):
            raise TypeError("ModuleReleaseSerializer is not validated")
        return UploadedModule(
            name=str(data["name"]),
            system=str(data["system"]),
            version=str(data["version"]),
            file=upload,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadedModule:
    """A module package as received."""

    name: str
    system: str
    version: str
    file: UploadedFile


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
        parsed = URL(super().validate_url(url))
        if parsed.scheme != "https" or not parsed.host:
            raise serializers.ValidationError(_("The registry URL must use https."))
        if parsed.query_string or parsed.fragment:
            raise serializers.ValidationError(_("The registry URL takes no query or fragment."))
        # An empty last segment gives the path its trailing slash, if it lacks one.
        return str(parsed if parsed.path.endswith("/") else parsed.joinpath(""))

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


class ModuleSelectionSerializer(serializers.Serializer[dict[str, object]]):
    """One module for a sync to fetch from the remote's registry."""

    namespace = serializers.CharField(help_text=_("Module namespace, e.g. cloudposse."))
    name = serializers.CharField(help_text=_("Module name, e.g. label."))
    system = serializers.CharField(help_text=_("Target system, e.g. null."))
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
    """Sync options: which providers, modules and platforms to fetch."""

    providers = ProviderSelectionSerializer(
        many=True,
        required=False,
        default=list,
        help_text=_("Providers to fetch; the registry offers no list of all providers."),
    )
    modules = ModuleSelectionSerializer(
        many=True,
        required=False,
        default=list,
        help_text=_(
            "Modules to fetch. Pulp checks out each version's source over HTTPS git or "
            "downloads its archive, and serves it as a package."
        ),
    )
    platforms = serializers.ListField(
        child=serializers.RegexField(r"^[a-z0-9]+_[a-z0-9]+$"),
        required=False,
        default=list,
        help_text=_("os_arch platforms to fetch, e.g. linux_amd64; empty fetches all."),
    )

    @override
    def validate(self, data: dict[str, object]) -> dict[str, object]:
        attrs = cast("dict[str, object]", super().validate(data))
        if not attrs.get("providers") and not attrs.get("modules"):
            raise serializers.ValidationError(_("Select at least one provider or module."))
        return attrs


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
    name = serializers.CharField(
        max_length=63,
        help_text=_(
            "The namespace in provider source addresses, e.g. acme in pulp.example/acme/widget: "
            "lowercase letters, digits and single dashes, no dash at either end."
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
    signing_service = serializers.HyperlinkedRelatedField(
        required=False,
        allow_null=True,
        view_name="signing-services-detail",
        queryset=models.TofuSigningService.objects.all(),
        help_text=_("A tofu signing service that signs releases published without a signature."),
    )

    def validate_name(self, name: str) -> str:
        """Accept only namespaces OpenTofu accepts in a source address."""
        if not is_provider_part(name):
            raise serializers.ValidationError(_("OpenTofu does not accept this namespace."))
        return name

    def validate_content_hostname(self, hostname: str) -> str:
        """Store the hostname as OpenTofu compares it."""
        if not hostname:
            return hostname
        try:
            return normalize_hostname(hostname)
        except ValueError as exc:
            raise serializers.ValidationError(
                _("Expected a hostname, with a port if not 443.")
            ) from exc

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
            "signing_service",
        )
        model = models.TofuNamespace


class TofuTokenSerializer(platform.ModelSerializer):
    """A registry token. The token itself appears only in the response that creates it."""

    pulp_href = platform.IdentityField(view_name="pulp_tofu/tokens-detail")
    name = serializers.CharField(max_length=128, help_text=_("A label for the token."))
    scope = serializers.ChoiceField(
        choices=["read", "publish"],
        default="read",
        help_text=_("read reads the namespaces; publish also publishes to them."),
    )
    namespaces = platform.RelatedField(
        many=True,
        view_name="pulp_tofu/namespaces-detail",
        queryset=models.TofuNamespace.objects.all(),
        help_text=_("The namespaces the token covers."),
    )
    expires_at = serializers.DateTimeField(
        required=False, allow_null=True, help_text=_("When the token stops working.")
    )
    prefix = serializers.CharField(read_only=True, help_text=_("The token's public prefix."))
    last_used_at = serializers.DateTimeField(read_only=True)
    token = serializers.SerializerMethodField(
        help_text=_("The bearer token to set as TF_TOKEN_<host>; returned only on creation.")
    )

    def get_token(self, instance: models.TofuToken) -> str | None:
        issued = self.context.get("issued_token")
        return issued.token if isinstance(issued, IssuedToken) else None

    def validate_namespaces(
        self, namespaces: list[models.TofuNamespace]
    ) -> list[models.TofuNamespace]:
        """Refuse namespaces the caller cannot see."""
        user = getattr(self.context.get("request"), "user", None)
        if not namespaces:
            raise serializers.ValidationError(_("Name at least one namespace."))
        for namespace in namespaces:
            if user is None or not (
                user.has_perm("tofu.view_tofunamespace")
                or user.has_perm("tofu.view_tofunamespace", namespace)
            ):
                raise serializers.ValidationError(_("Unknown namespace."))
        return namespaces

    @override
    def create(self, validated_data: dict[str, object]) -> models.TofuToken:
        namespaces = validated_data["namespaces"]
        if not isinstance(namespaces, list):
            raise TypeError("TofuTokenSerializer is not validated")
        covered = [
            n for n in cast("list[object]", namespaces) if isinstance(n, models.TofuNamespace)
        ]
        issued = issue_token()
        self.context["issued_token"] = issued
        token = models.TofuToken.objects.create(
            user=self.context["request"].user,
            name=str(validated_data["name"]),
            scope=str(validated_data["scope"]),
            expires_at=expires
            if isinstance(expires := validated_data.get("expires_at"), datetime)
            else None,
            prefix=issued.prefix,
            digest=issued.digest,
        )
        token.namespaces.set(covered)
        return token

    class Meta:
        fields = platform.ModelSerializer.Meta.fields + (
            "name",
            "scope",
            "namespaces",
            "expires_at",
            "prefix",
            "last_used_at",
            "token",
        )
        model = models.TofuToken


class ProviderReleaseSerializer(serializers.Serializer[object]):
    """The files of one provider release, as GoReleaser writes them to ``dist/``."""

    files = serializers.ListField(
        child=serializers.FileField(),
        allow_empty=False,
        help_text=_(
            "The platform zips, SHA256SUMS, SHA256SUMS.sig and the optional manifest.json "
            "of one release, under their release filenames. Leave out the signature, or "
            "the signature and SHA256SUMS, to have the namespace's signing service sign."
        ),
    )

    @override
    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        """Sort the files into a release; read the small ones, which travel to the task."""
        files = attrs["files"]
        if not isinstance(files, list):
            raise serializers.ValidationError({"files": _("Expected a list of files.")})
        by_name: dict[str, UploadedFile] = {}
        for upload in cast("list[object]", files):
            if not isinstance(upload, UploadedFile):
                raise serializers.ValidationError({"files": _("Expected a list of files.")})
            by_name[str(upload.name)] = upload
        try:
            release = parse_release(list(by_name))
        except ReleaseError as exc:
            raise serializers.ValidationError({"files": str(exc)}) from exc

        def read(name: str | None) -> bytes | None:
            if name is None:
                return None
            upload = by_name[name]
            if (upload.size or 0) > MAX_SMALL_FILE_BYTES:
                raise serializers.ValidationError({"files": f"{name} is too large"})
            return upload.read()

        return {
            "release": UploadedRelease(
                files=release,
                shasums=read(release.shasums),
                signature=read(release.signature),
                manifest=read(release.manifest),
                archives={p: by_name[name] for p, name in release.archives.items()},
            )
        }

    def uploaded_release(self) -> "UploadedRelease":
        """The validated release; call after ``is_valid``."""
        release = self.validated_data["release"]
        if not isinstance(release, UploadedRelease):
            raise TypeError("ProviderReleaseSerializer is not validated")
        return release


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadedRelease:
    """A release's files as received: the small ones read, the archives still uploads."""

    files: ReleaseFiles
    shasums: bytes | None
    signature: bytes | None
    manifest: bytes | None
    archives: dict[Platform, UploadedFile]
